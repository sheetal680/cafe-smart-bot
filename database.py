import sqlite3
import os
import json
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), 'cafe_smart.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            name TEXT,
            phone TEXT,
            email TEXT,
            inquiry_type TEXT DEFAULT 'general',
            message TEXT,
            source_page TEXT DEFAULT 'widget',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            name TEXT,
            phone TEXT,
            email TEXT,
            event_type TEXT,
            date TEXT,
            time TEXT,
            guests INTEGER,
            special_requests TEXT,
            status TEXT DEFAULT 'new',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS chat_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Indexes
    c.execute('CREATE INDEX IF NOT EXISTS idx_leads_created ON leads(created_at)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_leads_type ON leads(inquiry_type)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_bookings_created ON bookings(created_at)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_chat_logs_session ON chat_logs(session_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_analytics_type ON analytics(event_type)')

    # Migrations — safe to run on existing DBs
    for col_sql in [
        "ALTER TABLE leads ADD COLUMN contacted INTEGER DEFAULT 0",
        "ALTER TABLE leads ADD COLUMN contacted_at TIMESTAMP",
    ]:
        try:
            c.execute(col_sql)
        except Exception:
            pass  # Column already exists

    # chat_state table — server-side booking state machine
    c.execute('''
        CREATE TABLE IF NOT EXISTS chat_state (
            session_id TEXT PRIMARY KEY,
            state_data TEXT DEFAULT '{}',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # booking_ref migration
    try:
        c.execute("ALTER TABLE bookings ADD COLUMN booking_ref TEXT")
    except Exception:
        pass

    conn.commit()
    conn.close()


# ── Lead helpers ──────────────────────────────────────────────────────────────

def save_lead(session_id, name, phone, email, inquiry_type, message, source_page='widget'):
    conn = get_db()
    # Check if lead for this session already exists to avoid duplicates
    existing = conn.execute(
        'SELECT id FROM leads WHERE session_id = ?', (session_id,)
    ).fetchone()
    if existing:
        conn.execute(
            '''UPDATE leads SET name=COALESCE(NULLIF(?,\'\'), name),
               phone=COALESCE(NULLIF(?,\'\'), phone),
               email=COALESCE(NULLIF(?,\'\'), email),
               inquiry_type=?, message=?
               WHERE session_id=?''',
            (name, phone, email, inquiry_type, message, session_id)
        )
    else:
        conn.execute(
            '''INSERT INTO leads (session_id, name, phone, email, inquiry_type, message, source_page)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (session_id, name, phone, email, inquiry_type, message, source_page)
        )
    conn.commit()
    conn.close()


def get_lead_by_session(session_id):
    conn = get_db()
    row = conn.execute('SELECT * FROM leads WHERE session_id = ?', (session_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_leads(inquiry_type=None, contacted=None, limit=500):
    conn = get_db()
    where_clauses = []
    params = []

    if inquiry_type and inquiry_type != 'all':
        where_clauses.append('inquiry_type = ?')
        params.append(inquiry_type)
    if contacted == 'yes':
        where_clauses.append('contacted = 1')
    elif contacted == 'no':
        where_clauses.append('(contacted = 0 OR contacted IS NULL)')

    where_sql = ('WHERE ' + ' AND '.join(where_clauses)) if where_clauses else ''
    params.append(limit)
    rows = conn.execute(
        f'SELECT * FROM leads {where_sql} ORDER BY created_at DESC LIMIT ?', params
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_leads_stats():
    conn = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    week_ago = (datetime.now().replace(hour=0, minute=0, second=0) -
                timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')

    total = conn.execute('SELECT COUNT(*) FROM leads').fetchone()[0]
    today_count = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE date(created_at) = ?", (today,)
    ).fetchone()[0]
    week_count = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE created_at >= ?", (week_ago,)
    ).fetchone()[0]
    bookings_count = conn.execute('SELECT COUNT(*) FROM bookings').fetchone()[0]
    conn.close()
    return {'total': total, 'today': today_count, 'week': week_count, 'bookings': bookings_count}


def get_extended_stats():
    """Richer stats for the dashboard quick-stats section."""
    conn = get_db()
    today     = datetime.now().strftime('%Y-%m-%d')
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    week_ago  = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')

    today_leads     = conn.execute("SELECT COUNT(*) FROM leads WHERE date(created_at) = ?", (today,)).fetchone()[0]
    yesterday_leads = conn.execute("SELECT COUNT(*) FROM leads WHERE date(created_at) = ?", (yesterday,)).fetchone()[0]
    week_bookings   = conn.execute("SELECT COUNT(*) FROM bookings WHERE created_at >= ?", (week_ago,)).fetchone()[0]
    pending         = conn.execute("SELECT COUNT(*) FROM bookings WHERE status = 'new'").fetchone()[0]
    conversations   = conn.execute("SELECT COUNT(DISTINCT session_id) FROM chat_logs").fetchone()[0]
    uncontacted     = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE contacted = 0 OR contacted IS NULL"
    ).fetchone()[0]

    conn.close()
    diff = today_leads - yesterday_leads
    return {
        'today_leads':       today_leads,
        'yesterday_leads':   yesterday_leads,
        'today_diff':        diff,
        'today_trend':       'up' if diff >= 0 else 'down',
        'week_bookings':     week_bookings,
        'pending_bookings':  pending,
        'total_conversations': conversations,
        'uncontacted':       uncontacted,
    }


def get_new_leads_count():
    """Count of uncontacted leads — used for sidebar badge."""
    conn = get_db()
    count = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE contacted = 0 OR contacted IS NULL"
    ).fetchone()[0]
    conn.close()
    return count


def mark_lead_contacted(lead_id, contacted=True):
    conn = get_db()
    conn.execute(
        'UPDATE leads SET contacted = ?, contacted_at = ? WHERE id = ?',
        (1 if contacted else 0,
         datetime.now().isoformat() if contacted else None,
         lead_id)
    )
    conn.commit()
    conn.close()


# ── Booking helpers ───────────────────────────────────────────────────────────

def save_booking(session_id, name, phone, email, event_type, date, time, guests, special_requests, booking_ref=None):
    conn = get_db()
    existing = conn.execute(
        'SELECT id FROM bookings WHERE session_id = ?', (session_id,)
    ).fetchone()
    if existing:
        conn.execute(
            '''UPDATE bookings SET name=?, phone=?, email=?, event_type=?,
               date=?, time=?, guests=?, special_requests=?,
               booking_ref=COALESCE(?, booking_ref)
               WHERE session_id=?''',
            (name, phone, email, event_type, date, time, guests, special_requests, booking_ref, session_id)
        )
    else:
        conn.execute(
            '''INSERT INTO bookings (session_id, name, phone, email, event_type, date, time, guests, special_requests, booking_ref)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (session_id, name, phone, email, event_type, date, time, guests, special_requests, booking_ref)
        )
    conn.commit()
    conn.close()


def get_all_bookings(status=None, limit=500):
    conn = get_db()
    if status and status != 'all':
        rows = conn.execute(
            'SELECT * FROM bookings WHERE status = ? ORDER BY created_at DESC LIMIT ?',
            (status, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            'SELECT * FROM bookings ORDER BY created_at DESC LIMIT ?', (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_booking_status(booking_id, status):
    conn = get_db()
    conn.execute('UPDATE bookings SET status = ? WHERE id = ?', (status, booking_id))
    conn.commit()
    conn.close()


# ── Chat log helpers ──────────────────────────────────────────────────────────

def save_chat_message(session_id, role, message):
    conn = get_db()
    conn.execute(
        'INSERT INTO chat_logs (session_id, role, message) VALUES (?, ?, ?)',
        (session_id, role, message)
    )
    conn.commit()
    conn.close()


def get_chat_history(session_id, limit=50):
    conn = get_db()
    rows = conn.execute(
        'SELECT * FROM chat_logs WHERE session_id = ? ORDER BY created_at ASC LIMIT ?',
        (session_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_sessions(limit=200):
    conn = get_db()
    rows = conn.execute(
        '''SELECT session_id,
           COUNT(*) as message_count,
           MIN(created_at) as started_at,
           MAX(created_at) as last_message
           FROM chat_logs
           GROUP BY session_id
           ORDER BY last_message DESC
           LIMIT ?''',
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Analytics helpers ─────────────────────────────────────────────────────────

def log_analytics(event_type, data=''):
    conn = get_db()
    conn.execute('INSERT INTO analytics (event_type, data) VALUES (?, ?)', (event_type, data))
    conn.commit()
    conn.close()


def get_leads_per_day(days=30):
    conn = get_db()
    rows = conn.execute(
        '''SELECT date(created_at) as day, COUNT(*) as count
           FROM leads
           WHERE created_at >= date('now', ?)
           GROUP BY date(created_at)
           ORDER BY day ASC''',
        (f'-{days} days',)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_inquiry_breakdown():
    conn = get_db()
    rows = conn.execute(
        '''SELECT inquiry_type, COUNT(*) as count
           FROM leads GROUP BY inquiry_type ORDER BY count DESC'''
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_peak_hours():
    conn = get_db()
    rows = conn.execute(
        '''SELECT strftime('%H', created_at) as hour, COUNT(*) as count
           FROM chat_logs WHERE role = 'user'
           GROUP BY hour ORDER BY hour ASC'''
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_popular_topics():
    conn = get_db()
    rows = conn.execute(
        '''SELECT event_type as topic, COUNT(*) as count
           FROM analytics
           WHERE event_type LIKE 'topic_%'
           GROUP BY event_type ORDER BY count DESC LIMIT 10'''
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Chat state helpers ─────────────────────────────────────────────────────────

def get_chat_state(session_id):
    conn = get_db()
    row = conn.execute('SELECT state_data FROM chat_state WHERE session_id = ?', (session_id,)).fetchone()
    conn.close()
    if row and row[0]:
        try:
            return json.loads(row[0])
        except Exception:
            return {}
    return {}


def save_chat_state(session_id, state):
    conn = get_db()
    conn.execute(
        '''INSERT INTO chat_state (session_id, state_data, updated_at)
           VALUES (?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(session_id) DO UPDATE SET
               state_data = excluded.state_data,
               updated_at = excluded.updated_at''',
        (session_id, json.dumps(state))
    )
    conn.commit()
    conn.close()


def clear_chat_state(session_id):
    conn = get_db()
    conn.execute('DELETE FROM chat_state WHERE session_id = ?', (session_id,))
    conn.commit()
    conn.close()


# ── Booking notification helpers ───────────────────────────────────────────────

def get_bookings_since(since_id):
    """Return bookings with id > since_id — for dashboard polling."""
    conn = get_db()
    rows = conn.execute(
        'SELECT * FROM bookings WHERE id > ? ORDER BY id DESC LIMIT 10',
        (since_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_booking_id():
    conn = get_db()
    row = conn.execute('SELECT COALESCE(MAX(id), 0) FROM bookings').fetchone()
    conn.close()
    return row[0]
