/**
 * Cafe Smart Bot — Universal Embed Script
 *
 * Usage:
 *   <script src="https://yourdomain.com/static/embed.js"></script>
 *
 * Optional config before the script tag:
 *   <script>
 *     window.CafeBotConfig = {
 *       primaryColor: '#FF6B35',
 *       cafeName: 'Cafe Ice Magic',
 *     };
 *   </script>
 */
(function () {
  'use strict';

  // Prevent double-init
  if (window.__cafeBotEmbedded) return;
  window.__cafeBotEmbedded = true;

  const scriptEl = document.currentScript ||
    document.querySelector('script[src*="embed.js"]');

  // Detect host from the script src
  let HOST = '';
  if (scriptEl && scriptEl.src) {
    try {
      const url = new URL(scriptEl.src);
      HOST = url.origin;
    } catch (e) { HOST = ''; }
  }

  const userConfig = window.CafeBotConfig || {};
  const CAFE_NAME    = userConfig.cafeName    || scriptEl.getAttribute('data-cafe-name')    || 'Cafe';
  const PRIMARY      = userConfig.primaryColor || scriptEl.getAttribute('data-primary-color') || '#FF6B35';
  const GOLD         = '#FFD700';
  const DARK_BG      = '#1a1a2e';

  // ── Inject CSS ─────────────────────────────────────────────────────────────
  function injectStyles() {
    if (document.getElementById('cafe-bot-embed-css')) return;
    const link = document.createElement('link');
    link.id   = 'cafe-bot-embed-css';
    link.rel  = 'stylesheet';
    link.href = `${HOST}/static/css/widget.css`;
    document.head.appendChild(link);

    // FA icons
    if (!document.querySelector('link[href*="font-awesome"]')) {
      const fa = document.createElement('link');
      fa.rel  = 'stylesheet';
      fa.href = 'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css';
      document.head.appendChild(fa);
    }

    // Google Fonts (Poppins) if not loaded
    if (!document.querySelector('link[href*="Poppins"]')) {
      const gf = document.createElement('link');
      gf.rel  = 'stylesheet';
      gf.href = 'https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap';
      document.head.appendChild(gf);
    }
  }

  // ── Build widget HTML ──────────────────────────────────────────────────────
  function buildWidget() {
    const container = document.createElement('div');
    container.id = 'cafe-chat-widget';

    container.innerHTML = `
      <button class="chat-toggle" id="cafeBotToggle" aria-label="Chat with us">
        <span class="toggle-icon open-icon"><i class="fas fa-comment-dots"></i></span>
        <span class="toggle-icon close-icon" style="display:none"><i class="fas fa-times"></i></span>
        <span class="chat-badge" id="cafeBotBadge">1</span>
      </button>

      <div class="chat-window" id="cafeBotWindow">
        <div class="chat-header">
          <div class="chat-header-info">
            <div class="chat-avatar">✨</div>
            <div>
              <div class="chat-name">Maggie</div>
              <div class="chat-status">
                <span class="status-dot"></span>
                Online — ${escapeHtml(CAFE_NAME)}
              </div>
            </div>
          </div>
          <button class="chat-minimize" id="cafeBotMinimize">
            <i class="fas fa-minus"></i>
          </button>
        </div>

        <div class="chat-messages" id="cafeBotMessages">
          <div class="chat-date-divider">Today</div>
          <div class="message bot-message">
            <div class="message-bubble">
              Hey there! 👋 I'm <strong>Maggie</strong>, your assistant at <strong>${escapeHtml(CAFE_NAME)}</strong>!
              Ask me about our menu, timings, birthday packages, or anything else. ✨
            </div>
            <div class="message-time">Just now</div>
          </div>
        </div>

        <div class="typing-indicator" id="cafeBotTyping" style="display:none">
          <div class="typing-bubble"><span></span><span></span><span></span></div>
          <span class="typing-text">Maggie is typing...</span>
        </div>

        <div class="quick-replies" id="cafeBotQuickReplies">
          <button class="quick-reply" data-msg="Show me your menu">🍽️ Menu</button>
          <button class="quick-reply" data-msg="Birthday party packages">🎂 Birthday</button>
          <button class="quick-reply" data-msg="What are your timings?">🕐 Timings</button>
          <button class="quick-reply" data-msg="Current offers">🎁 Offers</button>
        </div>

        <div class="chat-input-area">
          <input type="text" class="chat-input" id="cafeBotInput" placeholder="Ask me anything..." autocomplete="off"/>
          <button class="chat-send" id="cafeBotSend" disabled>
            <i class="fas fa-paper-plane"></i>
          </button>
        </div>
      </div>
    `;

    document.body.appendChild(container);
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;')
      .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ── Init widget logic ──────────────────────────────────────────────────────
  function initWidget() {
    const SESSION_KEY = 'cafe_embed_session';
    let sessionId = sessionStorage.getItem(SESSION_KEY);
    if (!sessionId) {
      sessionId = 'emb_' + Math.random().toString(36).substr(2, 9) + '_' + Date.now();
      sessionStorage.setItem(SESSION_KEY, sessionId);
    }

    let isOpen = false, isTyping = false;

    const toggleBtn = document.getElementById('cafeBotToggle');
    const chatWindow = document.getElementById('cafeBotWindow');
    const messages   = document.getElementById('cafeBotMessages');
    const input      = document.getElementById('cafeBotInput');
    const sendBtn    = document.getElementById('cafeBotSend');
    const typingEl   = document.getElementById('cafeBotTyping');
    const quickReplies = document.getElementById('cafeBotQuickReplies');
    const badge      = document.getElementById('cafeBotBadge');
    const minimize   = document.getElementById('cafeBotMinimize');

    function openChat() {
      isOpen = true;
      chatWindow.classList.add('open');
      toggleBtn.querySelector('.open-icon').style.display  = 'none';
      toggleBtn.querySelector('.close-icon').style.display = 'flex';
      if (badge) badge.style.display = 'none';
      setTimeout(() => input && input.focus(), 350);
    }

    function closeChat() {
      isOpen = false;
      chatWindow.classList.remove('open');
      toggleBtn.querySelector('.open-icon').style.display  = 'flex';
      toggleBtn.querySelector('.close-icon').style.display = 'none';
    }

    toggleBtn.addEventListener('click', () => isOpen ? closeChat() : openChat());
    minimize.addEventListener('click', closeChat);

    function formatTime() {
      return new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
    }

    function appendMsg(text, role) {
      const div = document.createElement('div');
      div.className = `message ${role === 'user' ? 'user-message' : 'bot-message'}`;
      const formatted = text
        .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/\n/g, '<br>');
      div.innerHTML = `<div class="message-bubble">${formatted}</div><div class="message-time">${formatTime()}</div>`;
      messages.appendChild(div);
      messages.scrollTop = messages.scrollHeight;
      if (role === 'user') quickReplies.style.display = 'none';
    }

    async function send(text) {
      if (!text.trim() || isTyping) return;
      appendMsg(text, 'user');
      input.value = '';
      sendBtn.disabled = true;
      isTyping = true;
      typingEl.style.display = 'flex';
      messages.scrollTop = messages.scrollHeight;

      try {
        const res = await fetch(`${HOST}/chat`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: text, session_id: sessionId }),
        });
        const data = await res.json();
        if (data.session_id) {
          sessionId = data.session_id;
          sessionStorage.setItem(SESSION_KEY, sessionId);
        }
        typingEl.style.display = 'none';
        appendMsg(data.reply || 'I could not understand that. Please try again.', 'bot');
      } catch (e) {
        typingEl.style.display = 'none';
        appendMsg('Connection error. Please try again or call us directly! 📞', 'bot');
      } finally {
        isTyping = false;
        sendBtn.disabled = !input.value.trim();
        input.focus();
      }
    }

    sendBtn.addEventListener('click', () => send(input.value.trim()));
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); send(input.value.trim()); }
    });
    input.addEventListener('input', () => { sendBtn.disabled = !input.value.trim(); });

    quickReplies.querySelectorAll('.quick-reply').forEach(btn => {
      btn.addEventListener('click', () => {
        openChat();
        send(btn.getAttribute('data-msg'));
      });
    });

    // Expose
    window.CafeChatWidget = { open: openChat, close: closeChat, send };
  }

  // ── Boot ───────────────────────────────────────────────────────────────────
  function boot() {
    injectStyles();
    buildWidget();
    initWidget();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

})();
