/**
 * Cafe Smart Bot — Chat Widget v2
 * Supports booking mode state machine, quick replies, talk-to-staff
 */
(function () {
  'use strict';

  const scriptTag = document.getElementById('cafe-bot-widget-script');
  const HOST      = (scriptTag && scriptTag.getAttribute('data-host')) || '';
  const CAFE_NAME = (scriptTag && scriptTag.getAttribute('data-cafe-name')) || 'Cafe Ice Magic';
  const CAFE_PHONE= (scriptTag && scriptTag.getAttribute('data-phone'))     || '+91 98765 43210';

  // ── Session ─────────────────────────────────────────────────────────────────
  const SESSION_KEY = 'cafe_bot_session_id';
  let sessionId = sessionStorage.getItem(SESSION_KEY);
  if (!sessionId) {
    sessionId = 'sess_' + Math.random().toString(36).substr(2, 9) + '_' + Date.now();
    sessionStorage.setItem(SESSION_KEY, sessionId);
  }

  let isOpen    = false;
  let isTyping  = false;
  let bookingMode = false;   // true while server-side booking flow is active

  // ── DOM refs ─────────────────────────────────────────────────────────────────
  const toggleBtn    = document.getElementById('chatToggle');
  const chatWindow   = document.getElementById('chatWindow');
  const chatMessages = document.getElementById('chatMessages');
  const chatInput    = document.getElementById('chatInput');
  const chatSend     = document.getElementById('chatSend');
  const typingEl     = document.getElementById('typingIndicator');
  const quickReplies = document.getElementById('quickReplies');
  const chatBadge    = document.getElementById('chatBadge');
  const chatMinimize = document.getElementById('chatMinimize');
  const openIcon     = toggleBtn && toggleBtn.querySelector('.open-icon');
  const closeIcon    = toggleBtn && toggleBtn.querySelector('.close-icon');

  // ── Open / Close ─────────────────────────────────────────────────────────────
  function openChat() {
    if (!chatWindow) return;
    isOpen = true;
    chatWindow.classList.add('open');
    if (openIcon)  openIcon.style.display  = 'none';
    if (closeIcon) closeIcon.style.display = 'flex';
    if (chatBadge) chatBadge.style.display = 'none';
    setTimeout(() => chatInput && chatInput.focus(), 320);
  }

  function closeChat() {
    if (!chatWindow) return;
    isOpen = false;
    chatWindow.classList.remove('open');
    if (openIcon)  openIcon.style.display  = 'flex';
    if (closeIcon) closeIcon.style.display = 'none';
  }

  if (toggleBtn)  toggleBtn.addEventListener('click', () => isOpen ? closeChat() : openChat());
  if (chatMinimize) chatMinimize.addEventListener('click', closeChat);

  // ── Helpers ──────────────────────────────────────────────────────────────────
  function scrollBottom() {
    if (chatMessages) chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function formatTime() {
    return new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
  }

  function mdToHtml(text) {
    return text
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.*?)\*/g, '<em>$1</em>')
      .replace(/\n/g, '<br>');
  }

  // ── Append a normal chat bubble ───────────────────────────────────────────────
  function appendMessage(text, role) {
    if (!chatMessages) return;
    const div = document.createElement('div');
    div.className = `message ${role === 'user' ? 'user-message' : 'bot-message'}`;
    div.innerHTML = `<div class="message-bubble">${mdToHtml(text)}</div>
                     <div class="message-time">${formatTime()}</div>`;
    chatMessages.appendChild(div);
    scrollBottom();
    if (role === 'user') hideQuickReplies();
  }

  // ── Booking confirmation card ─────────────────────────────────────────────────
  function appendBookingCard(reply, ref) {
    if (!chatMessages) return;
    const div = document.createElement('div');
    div.className = 'message bot-message';
    div.innerHTML = `
      <div class="booking-confirm-card">
        <div class="bc-header">
          <span class="bc-checkmark">✅</span>
          <span class="bc-title">Booking Request Received!</span>
        </div>
        <div class="bc-ref">Reference: <strong>#${ref}</strong></div>
        <div class="bc-body">${mdToHtml(reply)}</div>
        <a href="https://wa.me/${CAFE_PHONE.replace(/\D/g,'')}" target="_blank" class="bc-whatsapp">
          <i class="fab fa-whatsapp"></i> Chat on WhatsApp
        </a>
      </div>
      <div class="message-time">${formatTime()}</div>`;
    chatMessages.appendChild(div);
    scrollBottom();
    bookingMode = false;
    showBookingProgress(null);   // hide progress bar
  }

  // ── Contact card (Talk to Staff) ──────────────────────────────────────────────
  function appendContactCard(phone, whatsapp) {
    if (!chatMessages) return;
    const ph = phone || CAFE_PHONE;
    const wa = whatsapp || ph;
    const div = document.createElement('div');
    div.className = 'message bot-message';
    div.innerHTML = `
      <div class="contact-card">
        <div class="cc-title">📞 Reach Us Directly</div>
        <a href="tel:${ph}" class="cc-btn cc-call"><i class="fas fa-phone"></i> Call ${ph}</a>
        <a href="https://wa.me/${wa.replace(/\D/g,'')}" target="_blank" class="cc-btn cc-wa">
          <i class="fab fa-whatsapp"></i> WhatsApp Us
        </a>
        <div class="cc-hours">Available: 11 AM – 11 PM daily</div>
      </div>
      <div class="message-time">${formatTime()}</div>`;
    chatMessages.appendChild(div);
    scrollBottom();
  }

  // ── Booking progress indicator ────────────────────────────────────────────────
  const STEPS = ['Guests', 'Date', 'Time', 'Contact', 'Requests'];
  function showBookingProgress(step) {
    let bar = document.getElementById('bookingProgress');
    if (!step) {
      if (bar) bar.remove();
      return;
    }
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'bookingProgress';
      bar.className = 'booking-progress';
      // Insert between messages and typing indicator
      if (typingEl && typingEl.parentNode) {
        typingEl.parentNode.insertBefore(bar, typingEl);
      }
    }
    bar.innerHTML = STEPS.map((s, i) => `
      <div class="bp-step ${i < step - 1 ? 'done' : i === step - 1 ? 'active' : ''}">
        <div class="bp-dot">${i < step - 1 ? '✓' : i + 1}</div>
        <div class="bp-label">${s}</div>
      </div>
    `).join('') + `<div class="bp-line" style="width:${Math.max(0,(step-1)/(STEPS.length-1)*100)}%"></div>`;
  }

  // ── Quick replies visibility ───────────────────────────────────────────────────
  function hideQuickReplies() {
    if (quickReplies) quickReplies.style.display = 'none';
  }

  // ── Typing indicator ──────────────────────────────────────────────────────────
  function showTyping() { if (typingEl) { typingEl.style.display = 'flex'; scrollBottom(); } }
  function hideTyping() { if (typingEl)   typingEl.style.display = 'none'; }

  // ── Send message ──────────────────────────────────────────────────────────────
  async function sendMessage(text) {
    if (!text.trim() || isTyping) return;

    appendMessage(text, 'user');
    if (chatInput) chatInput.value = '';
    if (chatSend)  chatSend.disabled = true;
    isTyping = true;
    showTyping();

    try {
      const endpoint = HOST ? `${HOST}/chat` : '/chat';
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ message: text, session_id: sessionId }),
      });

      if (!res.ok) throw new Error('Server error ' + res.status);
      const data = await res.json();

      if (data.session_id) {
        sessionId = data.session_id;
        sessionStorage.setItem(SESSION_KEY, sessionId);
      }

      hideTyping();

      // Handle special actions
      if (data.action === 'show_contact') {
        appendMessage(data.reply, 'bot');
        appendContactCard(data.phone, data.whatsapp);

      } else if (data.booking_complete) {
        appendBookingCard(data.reply, data.booking_ref);

      } else if (data.mode === 'booking') {
        bookingMode = true;
        appendMessage(data.reply, 'bot');
        showBookingProgress(data.step || 1);
        // Update placeholder to guide user
        const placeholders = {
          1: 'Enter number of guests (e.g. 20)...',
          2: 'Enter date (e.g. 25 Dec, next Saturday)...',
          3: 'Enter time (e.g. 7 PM, afternoon)...',
          4: 'Enter your name and phone number...',
          5: 'Any special requests? (or type "none")...',
        };
        if (chatInput) chatInput.placeholder = placeholders[data.step] || 'Your answer...';

      } else {
        // Normal message
        appendMessage(data.reply || 'Sorry, something went wrong.', 'bot');
        if (bookingMode) { bookingMode = false; showBookingProgress(null); }
        if (chatInput) chatInput.placeholder = 'Ask me anything...';
      }

    } catch (err) {
      hideTyping();
      appendMessage(
        `Oops! Connection issue. Please call us at **${CAFE_PHONE}** directly! 📞`,
        'bot'
      );
    } finally {
      isTyping = false;
      if (chatSend) chatSend.disabled = false;
      if (chatInput) chatInput.focus();
    }
  }

  // ── Input handlers ────────────────────────────────────────────────────────────
  if (chatSend) chatSend.addEventListener('click', () => chatInput && sendMessage(chatInput.value.trim()));
  if (chatInput) {
    chatInput.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(chatInput.value.trim()); }
    });
    chatInput.addEventListener('input', () => { if (chatSend) chatSend.disabled = !chatInput.value.trim(); });
  }
  if (chatSend) chatSend.disabled = true;

  // ── Quick reply buttons ───────────────────────────────────────────────────────
  if (quickReplies) {
    quickReplies.querySelectorAll('.quick-reply').forEach(btn => {
      btn.addEventListener('click', () => {
        const action = btn.getAttribute('data-action');
        const msg    = btn.getAttribute('data-msg');

        openChat();

        if (action === 'staff') {
          // Staff action — send special trigger
          sendMessage('__staff__');
        } else if (msg) {
          sendMessage(msg);
        }
      });
    });
  }

  // ── Nudge animation (first visit) ────────────────────────────────────────────
  setTimeout(() => {
    if (!isOpen && !sessionStorage.getItem('cafe_bot_visited')) {
      sessionStorage.setItem('cafe_bot_visited', '1');
      if (chatBadge) chatBadge.style.display = 'flex';
      if (toggleBtn) toggleBtn.classList.add('pulse-invite');
    }
  }, 5000);

  // ── Public API ────────────────────────────────────────────────────────────────
  window.CafeChatWidget = { open: openChat, close: closeChat, send: sendMessage };

})();
