import os
import json
import uuid
import csv
import io
import re
import random
from datetime import datetime
from functools import wraps

from flask import (Flask, request, jsonify, render_template, session,
                   redirect, url_for, Response)
from flask_cors import CORS
from dotenv import load_dotenv
from groq import Groq

import database as db

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'cafe-magic-secret-2024')
CORS(app, origins='*', supports_credentials=True)

# ── Startup ───────────────────────────────────────────────────────────────────
db.init_db()

with open('cafe_config.json', 'r', encoding='utf-8') as f:
    CAFE_CONFIG = json.load(f)

groq_client = Groq(api_key=os.getenv('GROQ_API_KEY', ''))

ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'cafeadmin123')

# ── Regex patterns ────────────────────────────────────────────────────────────
PHONE_RE = re.compile(r'(?:\+91[\s-]?)?[6-9]\d{9}')
EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
DATE_RE  = re.compile(
    r'\b(\d{1,2}[\/\-]\d{1,2}(?:[\/\-]\d{2,4})?'
    r'|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*'
    r'|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2}(?:st|nd|rd|th)?'
    r'|(?:today|tomorrow|this\s+\w+day|next\s+\w+day|this\s+weekend|next\s+weekend))\b',
    re.I
)
TIME_RE  = re.compile(r'\b(\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm)|afternoon|evening|morning|lunch|dinner)\b', re.I)
GUEST_RE = re.compile(r'\b(\d{1,3})\s*(?:guests?|people|persons?|pax|heads?|members?|friends?|family)?\b', re.I)
NAME_RE  = re.compile(
    r'(?:i(?:\'?m| am)|my name is|this is|call me|name[:\s]+|i\'?m)\s+([A-Za-z][a-z]+(?:\s+[A-Za-z][a-z]+)?)',
    re.I
)

CANCEL_WORDS = {'cancel', 'nevermind', 'never mind', 'stop', 'quit', 'exit',
                'forget it', 'no thanks', 'don\'t want', 'skip'}

STAFF_TRIGGERS = {'talk to staff', 'speak to staff', 'talk to human', 'speak to human',
                  'human agent', 'call me', 'contact staff', 'want to call', 'need to call',
                  'talk to someone', 'speak to someone', '__staff__'}

# ── Booking trigger detection ─────────────────────────────────────────────────
BOOKING_TRIGGERS = {
    'birthday_party': ['birthday', 'bday', 'b-day', 'birth day'],
    'event':          ['anniversary', 'corporate', 'function', 'kitty party',
                       'baby shower', 'farewell', 'get together', 'celebrate',
                       'event booking', 'book an event'],
    'reservation':    ['book a table', 'reserve a table', 'table booking',
                       'table reservation', 'reservation', 'book table',
                       'reserve', 'book a seat'],
}

BOOKING_PARTY_WORDS = ['party']   # 'party' can be birthday OR event — check birthday first


def is_booking_trigger(text):
    """Return event_type string if message triggers booking mode, else None."""
    tl = text.lower()
    for event_type, keywords in BOOKING_TRIGGERS.items():
        if any(kw in tl for kw in keywords):
            return event_type
    # 'party' alone → event (birthday already caught above)
    if 'party' in tl:
        return 'event'
    return None


def is_cancel(text):
    tl = text.lower().strip()
    return any(w in tl for w in CANCEL_WORDS)


def is_staff_request(text):
    tl = text.lower().strip()
    return any(w in tl for w in STAFF_TRIGGERS)


# ── Booking step handler ──────────────────────────────────────────────────────
def handle_booking_step(session_id, user_message, state):
    """
    Returns (reply_str, new_state_dict, booking_complete_bool).
    Mutates a copy of state.
    """
    step  = state.get('step', 1)
    bk    = dict(state)   # work on a copy

    # Allow cancellation at any step
    if is_cancel(user_message):
        db.clear_chat_state(session_id)
        return (
            "No problem at all! 😊 Your booking process has been cancelled. "
            "Feel free to ask me anything else — I'm here to help!",
            {}, False
        )

    # ── STEP 1: Guests ────────────────────────────────────────────────────────
    if step == 1:
        # Try "X guests/people" pattern first, then bare number
        m = re.search(r'\b(\d{1,3})\s*(?:guests?|people|persons?|pax|heads?|members?|friends?|family)?\b',
                      user_message, re.I)
        if m:
            bk['guests'] = int(m.group(1))
        else:
            return (
                "Could you tell me how many **guests** will be joining? 😊 "
                "(Just a number works, like \"20\" or \"15 people\")",
                state, False
            )
        bk['step'] = 2
        reply = (
            f"Great — **{bk['guests']} guests**! 🎉\n\n"
            f"What **date** are you thinking? _(e.g., 25 Dec, 5 Jan, this Saturday)_"
        )
        return reply, bk, False

    # ── STEP 2: Date ─────────────────────────────────────────────────────────
    if step == 2:
        dates = DATE_RE.findall(user_message)
        if dates:
            bk['date'] = dates[0]
        elif len(user_message.strip()) > 0:
            # Accept whatever they wrote — might be "next Saturday" etc.
            bk['date'] = user_message.strip()[:60]
        else:
            return (
                "What **date** works for you? _(e.g., 25 Dec, next Sunday, 5 Jan 2025)_",
                state, False
            )
        bk['step'] = 3
        reply = (
            f"📅 Perfect — **{bk['date']}**!\n\n"
            f"What **time** would you prefer? _(e.g., 7 PM, 6:30 PM, evening)_"
        )
        return reply, bk, False

    # ── STEP 3: Time ─────────────────────────────────────────────────────────
    if step == 3:
        times = TIME_RE.findall(user_message)
        if times:
            bk['time'] = times[0]
        elif len(user_message.strip()) > 0:
            bk['time'] = user_message.strip()[:30]
        else:
            return (
                "What **time** works best? _(e.g., 7 PM, 6:30 PM, afternoon, evening)_",
                state, False
            )
        bk['step'] = 4
        reply = (
            f"⏰ Noted — **{bk['time']}**! Almost done...\n\n"
            f"Could I get your **name and phone number** to confirm the booking? 😊"
        )
        return reply, bk, False

    # ── STEP 4: Contact (name + phone) ───────────────────────────────────────
    if step == 4:
        phones = PHONE_RE.findall(user_message)
        nm     = NAME_RE.search(user_message)
        emails = EMAIL_RE.findall(user_message)

        if phones:
            bk['phone'] = phones[0]
        if nm:
            bk['name'] = nm.group(1).strip().title()
        if emails:
            bk['email'] = emails[0]

        # Try to extract a standalone name if no pattern matched
        if not nm and phones:
            words = re.findall(r'\b[A-Z][a-z]{2,}\b', user_message)
            if words:
                bk['name'] = words[0].title()

        if not bk.get('phone') and not bk.get('name'):
            return (
                "I need your **name and phone number** to confirm — could you share both? 😊\n"
                "_(e.g., \"Priya, 9876543210\")_",
                state, False
            )
        if not bk.get('phone'):
            return (
                f"Got your name **{bk.get('name')}** — now could I also get your **phone number**? 📞",
                state, False
            )

        bk['step'] = 5
        event_label = {
            'birthday_party': '🎂 birthday party',
            'reservation':    '📅 table reservation',
            'event':          '🎉 event',
        }.get(bk.get('event_type', ''), '✨ booking')
        reply = (
            f"Almost there! Just one more thing — any **special requests** for your {event_label}?\n\n"
            f"_(e.g., custom cake message, DJ, balloon colour, dietary requirements, surprise setup)_\n\n"
            f"Or type **none** if everything is fine as-is!"
        )
        return reply, bk, False

    # ── STEP 5: Special requests → COMPLETE ──────────────────────────────────
    if step == 5:
        msg_lower = user_message.lower().strip()
        if msg_lower in ('none', 'no', 'nope', 'nothing', 'no thanks', 'nahi', 'nothing special', 'all good'):
            bk['special_requests'] = 'No special requests'
        else:
            bk['special_requests'] = user_message.strip()[:300]

        # Generate booking reference
        ref_num  = random.randint(1000, 9999)
        booking_ref = f"CIM-{ref_num}"
        bk['booking_ref'] = booking_ref
        bk['mode']  = 'complete'

        # Save lead
        db.save_lead(
            session_id=session_id,
            name=bk.get('name', ''),
            phone=bk.get('phone', ''),
            email=bk.get('email', ''),
            inquiry_type=bk.get('event_type', 'reservation'),
            message=(f"Booking via chat: {bk.get('guests')} guests, "
                     f"{bk.get('date')}, {bk.get('time')}"),
        )
        # Save booking
        db.save_booking(
            session_id=session_id,
            name=bk.get('name', ''),
            phone=bk.get('phone', ''),
            email=bk.get('email', ''),
            event_type=bk.get('event_type', 'reservation'),
            date=bk.get('date', ''),
            time=bk.get('time', ''),
            guests=int(bk.get('guests', 0)) if bk.get('guests') else 0,
            special_requests=bk.get('special_requests', ''),
            booking_ref=booking_ref,
        )
        db.clear_chat_state(session_id)

        name_part = f", **{bk['name']}**" if bk.get('name') else ""
        phone_display = bk.get('phone') or 'your number'
        reply = (
            f"✅ **Booking Request Received{name_part}!**\n\n"
            f"🎫 **Reference:** #{booking_ref}\n"
            f"👥 **Guests:** {bk.get('guests')}\n"
            f"📅 **Date:** {bk.get('date')}\n"
            f"⏰ **Time:** {bk.get('time')}\n"
            f"🎁 **Special Requests:** {bk.get('special_requests')}\n\n"
            f"Our team will call you at **{phone_display}** within **30 minutes** to confirm. "
            f"We can't wait to host you at {CAFE_CONFIG['name']}! 🎉"
        )
        return reply, bk, True

    # Fallback
    db.clear_chat_state(session_id)
    return (
        "I seem to have lost track of where we were! Let's start fresh — "
        "what can I help you with? 😊",
        {}, False
    )


# ── System prompt builder ─────────────────────────────────────────────────────
def build_system_prompt(booking_mode=False):
    cfg = CAFE_CONFIG
    menu_lines = []
    for cat, items in cfg['menu'].items():
        menu_lines.append(f"\n{cat}:")
        for item in items:
            menu_lines.append(f"  • {item['name']}: ₹{item['price']} — {item.get('description', '')}")

    pkg_lines = []
    for p in cfg['birthday_packages']:
        pkg_lines.append(
            f"  • {p['name']}: ₹{p['price']} (up to {p['max_guests']} guests) — {p['description'][:120]}..."
        )

    timings_lines = [f"  {d}: {t}" for d, t in cfg['timings'].items()]

    offers_lines = []
    for o in cfg['offers']:
        offers_lines.append(f"  • {o['name']}: {o['description']} | {o['validity']} | Code: {o['code']}")

    return f"""You are Maggie, the friendly AI assistant for {cfg['name']} — {cfg['tagline']}.
{cfg['description']}

📍 Location: {cfg['location']['address']}
Landmark: {cfg['location']['landmark']}
Parking: {cfg['location']['parking']}

📞 Contact: {cfg['contact']['phone']} | {cfg['contact']['email']}
📱 Instagram: {cfg['contact']['instagram']}

🕐 Timings:
{chr(10).join(timings_lines)}

🍽️ MENU:
{''.join(menu_lines)}

🎂 BIRTHDAY PACKAGES:
{chr(10).join(pkg_lines)}

🎉 EVENTS: {cfg['event_booking']['description']}
Advance notice: {cfg['event_booking']['advance_notice']}

🎁 CURRENT OFFERS:
{chr(10).join(offers_lines)}

✨ Special Features: {', '.join(cfg['special_features'])}

WiFi: {cfg['wifi']}

== YOUR BEHAVIOUR RULES ==
1. You are warm, enthusiastic, and helpful — like a friendly cafe host.
2. Answer questions accurately using ONLY the data above. Never make up prices or info.
3. After the customer's 2nd message, if you don't have their contact info yet, naturally ask for their name and phone number.
4. When customer asks about specific menu items and seems interested, ALWAYS end your response with: "Would you like to reserve a table to try this? I can help you book right now! 🪑"
5. Keep responses concise — 2-4 sentences max unless listing items.
6. Use emojis sparingly to stay warm and engaging.
7. If asked something you don't know, say "Let me have our team call you back with that info!"
8. NEVER reveal you are an AI, Groq, or LLaMA. You are "Maggie from {cfg['name']}".
9. NEVER handle bookings yourself in this mode — if user mentions birthday/party/event/reservation, just say "I'd be happy to help you book! Just type 'Book a Table' or 'Birthday Packages' below and I'll guide you step by step!"
"""


def detect_topics(text):
    topics = []
    tl = text.lower()
    if any(w in tl for w in ['pizza', 'burger', 'shake', 'ice cream', 'waffle', 'dessert', 'menu', 'food']):
        topics.append('topic_menu')
    if any(w in tl for w in ['birthday', 'party', 'package']):
        topics.append('topic_birthday')
    if any(w in tl for w in ['timing', 'open', 'close', 'hours']):
        topics.append('topic_timings')
    if any(w in tl for w in ['location', 'address', 'where', 'parking']):
        topics.append('topic_location')
    if any(w in tl for w in ['offer', 'discount', 'deal', 'promo']):
        topics.append('topic_offers')
    if any(w in tl for w in ['book', 'reservation', 'reserve', 'event']):
        topics.append('topic_booking')
    return topics


# ── Context processor: inject sidebar badge into every admin template ─────────
@app.context_processor
def inject_sidebar_stats():
    if session.get('admin_logged_in'):
        return {'new_leads_count': db.get_new_leads_count()}
    return {'new_leads_count': 0}


# ── Auth decorator ────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return render_template('index.html', cafe=CAFE_CONFIG)


# ── Chat API ──────────────────────────────────────────────────────────────────
@app.route('/chat', methods=['POST'])
def chat():
    data         = request.get_json(force=True)
    user_message = data.get('message', '').strip()
    session_id   = data.get('session_id') or str(uuid.uuid4())

    if not user_message:
        return jsonify({'error': 'Empty message'}), 400

    # ── Handle "Talk to Staff" special trigger ────────────────────────────────
    if is_staff_request(user_message):
        db.save_chat_message(session_id, 'user', user_message)
        cfg = CAFE_CONFIG
        # Save a lead so the owner sees it
        lead = db.get_lead_by_session(session_id)
        if not lead:
            db.save_lead(session_id, '', '', '', 'staff_request',
                         'Customer requested to talk to staff', 'widget')
        reply = (
            f"📞 **Sure! Here's how to reach us directly:**\n\n"
            f"**Phone / WhatsApp:** {cfg['contact']['phone']}\n"
            f"**Email:** {cfg['contact']['email']}\n"
            f"**Instagram:** {cfg['contact']['instagram']}\n\n"
            f"We're available {cfg['timings']['Monday']} (Mon–Thu) and "
            f"{cfg['timings']['Saturday']} (Sat). "
            f"Our team will be happy to assist you! 😊"
        )
        db.save_chat_message(session_id, 'assistant', reply)
        db.log_analytics('topic_staff_request', user_message[:100])
        return jsonify({
            'reply': reply,
            'session_id': session_id,
            'action': 'show_contact',
            'phone': cfg['contact']['phone'],
            'whatsapp': cfg['contact']['whatsapp'],
        })

    # ── Load booking state ────────────────────────────────────────────────────
    state = db.get_chat_state(session_id)

    # ── BOOKING MODE — handle step-by-step ───────────────────────────────────
    if state.get('mode') == 'booking':
        db.save_chat_message(session_id, 'user', user_message)
        reply, new_state, complete = handle_booking_step(session_id, user_message, state)
        db.save_chat_message(session_id, 'assistant', reply)
        if new_state and new_state.get('mode') != 'complete':
            db.save_chat_state(session_id, new_state)

        response = {'reply': reply, 'session_id': session_id, 'mode': 'booking'}
        if complete:
            response['booking_complete'] = True
            response['booking_ref']      = new_state.get('booking_ref')
            response['mode']             = 'complete'
        else:
            response['step'] = new_state.get('step', state.get('step', 1))
        return jsonify(response)

    # ── Check if this message triggers BOOKING MODE ───────────────────────────
    event_type = is_booking_trigger(user_message)
    if event_type:
        db.save_chat_message(session_id, 'user', user_message)
        db.log_analytics('topic_booking', user_message[:200])

        new_state = {
            'mode':       'booking',
            'step':       1,
            'event_type': event_type,
            'guests':     None,
            'date':       None,
            'time':       None,
            'name':       None,
            'phone':      None,
            'email':      None,
            'special_requests': None,
        }
        db.save_chat_state(session_id, new_state)

        if event_type == 'birthday_party':
            reply = (
                "🎂 Ooh, a **birthday celebration** — how exciting! I'd love to help you plan the perfect party!\n\n"
                "Let's get you booked in just a few quick steps. "
                "First — **how many guests** are you expecting? 🎉"
            )
        elif event_type == 'reservation':
            reply = (
                "📅 **Table reservation** — perfect! Let me get that sorted for you in just a moment.\n\n"
                "**How many guests** will be joining? 😊"
            )
        else:
            reply = (
                "🎉 Sounds like a wonderful **celebration**! I'll help you book it step by step.\n\n"
                "First — **how many guests** are you expecting?"
            )

        db.save_chat_message(session_id, 'assistant', reply)
        return jsonify({
            'reply':      reply,
            'session_id': session_id,
            'mode':       'booking',
            'step':       1,
            'event_type': event_type,
        })

    # ── NORMAL AI MODE ────────────────────────────────────────────────────────
    history    = db.get_chat_history(session_id, limit=20)
    messages   = [{'role': r['role'], 'content': r['message']} for r in history]
    user_count = sum(1 for m in messages if m['role'] == 'user')

    messages.append({'role': 'user', 'content': user_message})
    db.save_chat_message(session_id, 'user', user_message)

    for topic in detect_topics(user_message):
        db.log_analytics(topic, user_message[:200])

    system_prompt  = build_system_prompt()
    lead           = db.get_lead_by_session(session_id)
    contact_ok     = bool(lead and (lead.get('phone') or lead.get('email')))

    if user_count >= 2 and not contact_ok:
        system_prompt += (
            "\n\nIMPORTANT: The customer has sent several messages and we don't have their contact info yet. "
            "Naturally weave in asking for their name and phone number at the end of your response."
        )

    try:
        completion = groq_client.chat.completions.create(
            model='llama-3.3-70b-versatile',   # ✅ FIXED MODEL NAME
            messages=[{'role': 'system', 'content': system_prompt}] + messages,
            temperature=0.7,
            max_tokens=512,
        )
        reply = completion.choices[0].message.content.strip()
    except Exception as e:
        reply = (
            f"Hey there! 👋 I'm having a tiny technical hiccup. "
            f"Please call us at {CAFE_CONFIG['contact']['phone']} or WhatsApp us — we'd love to help!"
        )
        app.logger.error(f"Groq API error: {e}")

    db.save_chat_message(session_id, 'assistant', reply)

    # Extract contact info from all user messages
    all_user_text = ' '.join(m['content'] for m in messages if m['role'] == 'user')
    phones  = PHONE_RE.findall(all_user_text)
    emails  = EMAIL_RE.findall(all_user_text)
    nm      = NAME_RE.search(all_user_text)
    name    = nm.group(1).strip().title() if nm else (lead.get('name') if lead else '')

    if phones or emails:
        inquiry_type = 'general'
        tl = all_user_text.lower()
        if any(w in tl for w in ['birthday', 'bday', 'party', 'celebration']):
            inquiry_type = 'birthday_party'
        elif any(w in tl for w in ['book', 'reservation', 'reserve', 'table']):
            inquiry_type = 'reservation'
        elif any(w in tl for w in ['event', 'corporate', 'anniversary']):
            inquiry_type = 'event'
        elif any(w in tl for w in ['menu', 'food', 'pizza', 'burger']):
            inquiry_type = 'menu_inquiry'

        db.save_lead(
            session_id=session_id,
            name=name or '',
            phone=phones[0] if phones else '',
            email=emails[0] if emails else '',
            inquiry_type=inquiry_type,
            message=user_message[:500],
        )

    return jsonify({'reply': reply, 'session_id': session_id})


# ── Embeddable script ─────────────────────────────────────────────────────────
@app.route('/static/embed.js')
def embed_js():
    host = request.host_url.rstrip('/')
    js = f"""
(function() {{
  var CAFE_BOT_HOST = '{host}';
  if (window.__cafeBotEmbedded) return;
  window.__cafeBotEmbedded = true;
  var link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = CAFE_BOT_HOST + '/static/css/widget.css';
  document.head.appendChild(link);
  var script = document.createElement('script');
  script.id = 'cafe-bot-widget-script';
  script.src = CAFE_BOT_HOST + '/static/js/widget.js';
  script.setAttribute('data-host', CAFE_BOT_HOST);
  script.setAttribute('data-cafe-name', '{CAFE_CONFIG["name"]}');
  script.setAttribute('data-phone', '{CAFE_CONFIG["contact"]["phone"]}');
  document.body.appendChild(script);
}})();
"""
    return Response(js, mimetype='application/javascript')


# ══════════════════════════════════════════════════════════════════════════════
#  ADMIN ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            session['admin_user'] = username
            return redirect(url_for('admin_dashboard'))
        error = 'Invalid credentials. Please try again.'
    return render_template('admin/login.html', error=error, cafe=CAFE_CONFIG)


@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))


@app.route('/admin')
@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    stats          = db.get_leads_stats()
    ext_stats      = db.get_extended_stats()
    recent_leads   = db.get_all_leads(limit=5)
    recent_bookings = db.get_all_bookings(limit=5)
    sparkline      = db.get_leads_per_day(7)
    latest_booking_id = db.get_latest_booking_id()
    return render_template('admin/dashboard.html',
                           cafe=CAFE_CONFIG, stats=stats,
                           ext_stats=ext_stats,
                           sparkline=sparkline,
                           recent_leads=recent_leads,
                           recent_bookings=recent_bookings,
                           latest_booking_id=latest_booking_id)


@app.route('/admin/leads')
@login_required
def admin_leads():
    inquiry_type = request.args.get('type', 'all')
    contacted    = request.args.get('contacted', None)
    leads = db.get_all_leads(inquiry_type=inquiry_type, contacted=contacted)
    return render_template('admin/leads.html', cafe=CAFE_CONFIG,
                           leads=leads, current_filter=inquiry_type,
                           contacted_filter=contacted)


@app.route('/admin/bookings')
@login_required
def admin_bookings():
    status   = request.args.get('status', 'all')
    bookings = db.get_all_bookings(status=status)
    return render_template('admin/bookings.html', cafe=CAFE_CONFIG,
                           bookings=bookings, current_filter=status)


@app.route('/admin/chat-logs')
@login_required
def admin_chat_logs():
    sessions = db.get_all_sessions()
    return render_template('admin/chat_logs.html', cafe=CAFE_CONFIG, sessions=sessions)


@app.route('/admin/analytics')
@login_required
def admin_analytics():
    stats = db.get_leads_stats()
    return render_template('admin/analytics.html', cafe=CAFE_CONFIG, stats=stats)


# ── Admin API endpoints ───────────────────────────────────────────────────────

@app.route('/api/booking-status', methods=['POST'])
@login_required
def update_booking_status():
    data       = request.get_json(force=True)
    booking_id = data.get('id')
    status     = data.get('status')
    if not booking_id or status not in ('new', 'confirmed', 'cancelled'):
        return jsonify({'error': 'Invalid data'}), 400
    db.update_booking_status(booking_id, status)
    return jsonify({'success': True})


@app.route('/api/lead-contacted', methods=['POST'])
@login_required
def toggle_lead_contacted():
    data      = request.get_json(force=True)
    lead_id   = data.get('id')
    contacted = data.get('contacted', True)
    if not lead_id:
        return jsonify({'error': 'Missing id'}), 400
    db.mark_lead_contacted(lead_id, bool(contacted))
    return jsonify({'success': True, 'contacted': bool(contacted)})


@app.route('/api/chat-session/<session_id>')
@login_required
def get_chat_session(session_id):
    messages = db.get_chat_history(session_id)
    return jsonify(messages)


@app.route('/api/new-bookings')
@login_required
def new_bookings_poll():
    """Polling endpoint — returns bookings with id > since."""
    since    = request.args.get('since', 0, type=int)
    bookings = db.get_bookings_since(since)
    return jsonify(bookings)


@app.route('/api/analytics-data')
@login_required
def analytics_data():
    return jsonify({
        'leads_per_day':     db.get_leads_per_day(30),
        'inquiry_breakdown': db.get_inquiry_breakdown(),
        'peak_hours':        db.get_peak_hours(),
        'popular_topics':    db.get_popular_topics(),
    })


@app.route('/api/export-leads')
@login_required
def export_leads():
    leads  = db.get_all_leads(limit=10000)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        'id', 'name', 'phone', 'email', 'inquiry_type',
        'message', 'source_page', 'contacted', 'contacted_at', 'created_at'
    ])
    writer.writeheader()
    for lead in leads:
        writer.writerow({k: lead.get(k, '') for k in writer.fieldnames})
    output.seek(0)
    filename = f"leads_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@app.route('/api/export-bookings')
@login_required
def export_bookings():
    bookings = db.get_all_bookings(limit=10000)
    output   = io.StringIO()
    writer   = csv.DictWriter(output, fieldnames=[
        'id', 'name', 'phone', 'email', 'event_type',
        'date', 'time', 'guests', 'special_requests',
        'booking_ref', 'status', 'created_at'
    ])
    writer.writeheader()
    for b in bookings:
        writer.writerow({k: b.get(k, '') for k in writer.fieldnames})
    output.seek(0)
    filename = f"bookings_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)