/*
 * Website chatbot widget (proof of concept).
 *
 * Drop-in embed:
 *   <script src="https://YOUR-HOST/widget.js" data-api="https://YOUR-HOST"
 *           data-site="<site_id>" data-title="Ask us" data-color="#2563eb" defer></script>
 *
 * One file, no framework, Shadow DOM so the host page's CSS cannot break it.
 * Model text is escaped before a tiny formatter adds bold, links and lists.
 */
(function () {
  'use strict';

  var script = document.currentScript;
  var ds = (script && script.dataset) || {};
  var config = {
    api: (ds.api || (script && script.src ? new URL(script.src).origin : '') || '').replace(/\/$/, ''),
    site: ds.site || '',
    title: ds.title || 'Ask us',
    color: ds.color || '#2563eb',
    debug: ds.debug === '1' || /[?&]debug=1(&|$)/.test(window.location.search)
  };

  // ---------------------------------------------------------------- helpers

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
    });
  }

  function storageGet(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }
  function storageSet(key, value) {
    try { window.localStorage.setItem(key, value); } catch (e) { /* private mode */ }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function safeUrl(url) {
    try {
      var u = new URL(url, window.location.href);
      return (u.protocol === 'http:' || u.protocol === 'https:' || u.protocol === 'mailto:') ? u.href : null;
    } catch (e) { return null; }
  }

  /* Escape first, then format. Never puts raw model output into innerHTML. */
  function formatAnswer(text, sources) {
    var html = escapeHtml(text);
    html = html.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, function (m, label, url) {
      var safe = safeUrl(url);
      return safe ? '<a href="' + escapeHtml(safe) + '" target="_blank" rel="noopener noreferrer">' + label + '</a>' : label;
    });
    html = html.replace(/\[(\d{1,2})\]/g, function (m, n) {
      var src = (sources || []).filter(function (s) { return String(s.number) === n; })[0];
      if (!src) return '';
      var safe = safeUrl(src.url);
      return safe ? ' <a class="cite" href="' + escapeHtml(safe) + '" target="_blank" rel="noopener noreferrer" title="' + escapeHtml(src.title || '') + '">' + n + '</a>' : '';
    });
    var lines = html.split('\n');
    var out = [], inList = null;
    function closeList() { if (inList) { out.push('</' + inList + '>'); inList = null; } }
    lines.forEach(function (line) {
      var ul = /^\s*[-*•]\s+(.*)$/.exec(line);
      var ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
      if (ul || ol) {
        var tag = ul ? 'ul' : 'ol';
        if (inList !== tag) { closeList(); out.push('<' + tag + '>'); inList = tag; }
        out.push('<li>' + (ul || ol)[1] + '</li>');
      } else {
        closeList();
        if (line.trim() === '') out.push('<br>'); else out.push('<p>' + line + '</p>');
      }
    });
    closeList();
    return out.join('').replace(/(<br>){2,}/g, '<br>');
  }

  function fmtMs(v) { return (v === null || v === undefined) ? 'n/a' : v + ' ms'; }

  // ---------------------------------------------------------------- styles

  var CSS = '\
:host { all: initial; }\
*, *::before, *::after { box-sizing: border-box; }\
.root { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; font-size: 15px; line-height: 1.45; color: #111827; }\
.bubble { position: fixed; right: 20px; bottom: 20px; width: 56px; height: 56px; border-radius: 50%; border: 0; cursor: pointer; background: var(--c); color: #fff; box-shadow: 0 8px 24px rgba(0,0,0,.18); display: flex; align-items: center; justify-content: center; z-index: 2147483000; transition: transform .15s; }\
.bubble:hover { transform: scale(1.05); }\
.bubble:focus-visible, button:focus-visible, a:focus-visible, textarea:focus-visible { outline: 3px solid #93c5fd; outline-offset: 2px; }\
.bubble svg { width: 26px; height: 26px; }\
.panel { position: fixed; right: 20px; bottom: 90px; width: 380px; height: 560px; max-height: calc(100vh - 110px); background: #fff; border-radius: 16px; box-shadow: 0 16px 48px rgba(0,0,0,.22); display: none; flex-direction: column; overflow: hidden; z-index: 2147483001; }\
.panel.open { display: flex; }\
.head { background: var(--c); color: #fff; padding: 12px 14px; display: flex; align-items: center; gap: 8px; flex: 0 0 auto; }\
.head h2 { font-size: 16px; margin: 0; flex: 1; font-weight: 600; }\
.head button { background: rgba(255,255,255,.18); border: 0; color: #fff; width: 32px; height: 32px; border-radius: 8px; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; font-size: 14px; }\
.head button[aria-pressed="true"] { background: #fff; color: var(--c); }\
.head button:hover { background: rgba(255,255,255,.3); }\
.head button[aria-pressed="true"]:hover { background: #f3f4f6; }\
.msgs { flex: 1 1 auto; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; background: #f9fafb; }\
.msg { max-width: 88%; padding: 9px 12px; border-radius: 14px; word-wrap: break-word; overflow-wrap: anywhere; }\
.msg p { margin: 0 0 6px; } .msg p:last-child { margin-bottom: 0; }\
.msg ul, .msg ol { margin: 4px 0 6px 18px; padding: 0; }\
.msg.user { align-self: flex-end; background: var(--c); color: #fff; border-bottom-right-radius: 4px; }\
.msg.bot { align-self: flex-start; background: #fff; border: 1px solid #e5e7eb; border-bottom-left-radius: 4px; }\
.msg.err { align-self: flex-start; background: #fef2f2; border: 1px solid #fecaca; color: #991b1b; }\
.msg a { color: #1d4ed8; }\
.cite { display: inline-block; min-width: 18px; height: 18px; line-height: 18px; padding: 0 5px; border-radius: 9px; background: #e0e7ff; color: #3730a3 !important; font-size: 11px; font-weight: 600; text-align: center; text-decoration: none; vertical-align: text-top; }\
.sources { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }\
.sources a { display: inline-flex; align-items: center; gap: 5px; max-width: 100%; font-size: 12px; text-decoration: none; color: #1f2937; background: #f3f4f6; border: 1px solid #e5e7eb; padding: 3px 8px 3px 4px; border-radius: 999px; }\
.sources a:hover { background: #e5e7eb; }\
.sources .n { background: var(--c); color: #fff; border-radius: 50%; width: 16px; height: 16px; font-size: 10px; display: inline-flex; align-items: center; justify-content: center; flex: 0 0 auto; }\
.sources .t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 200px; }\
.typing span { display: inline-block; width: 7px; height: 7px; margin-right: 3px; border-radius: 50%; background: #9ca3af; animation: blink 1.2s infinite; }\
.typing span:nth-child(2) { animation-delay: .2s; } .typing span:nth-child(3) { animation-delay: .4s; }\
@keyframes blink { 0%, 80%, 100% { opacity: .25; } 40% { opacity: 1; } }\
.retry { margin-top: 6px; background: #fff; border: 1px solid #fca5a5; color: #991b1b; border-radius: 8px; padding: 4px 10px; cursor: pointer; font-size: 13px; }\
.debug { font-size: 12px; margin-top: 8px; border-top: 1px dashed #e5e7eb; padding-top: 6px; color: #374151; }\
.debug summary { cursor: pointer; font-weight: 600; color: #4b5563; }\
.debug dl { display: grid; grid-template-columns: auto 1fr; gap: 2px 10px; margin: 6px 0; }\
.debug dt { color: #6b7280; } .debug dd { margin: 0; }\
.debug .chunk { background: #f3f4f6; border-radius: 6px; padding: 6px; margin-top: 6px; }\
.debug .chunk .meta { color: #6b7280; font-size: 11px; margin-bottom: 2px; word-break: break-all; }\
.debug .chunk a { color: #1d4ed8; }\
.compose { display: flex; gap: 8px; padding: 10px; border-top: 1px solid #e5e7eb; background: #fff; flex: 0 0 auto; }\
.compose textarea { flex: 1; resize: none; border: 1px solid #d1d5db; border-radius: 10px; padding: 9px 11px; font: inherit; max-height: 96px; min-height: 40px; line-height: 1.35; }\
.compose button { background: var(--c); border: 0; color: #fff; border-radius: 10px; width: 44px; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; flex: 0 0 auto; }\
.compose button:disabled { opacity: .5; cursor: default; }\
.foot { font-size: 11px; color: #6b7280; text-align: center; padding: 4px 8px 7px; background: #fff; }\
.foot a { color: inherit; }\
@media (max-width: 480px) {\
  .panel { right: 0; bottom: 0; left: 0; top: 0; width: 100%; height: 100%; max-height: none; border-radius: 0; }\
  .panel.open ~ .bubble { display: none; }\
}\
@media (prefers-reduced-motion: reduce) { .bubble { transition: none; } .typing span { animation: none; } }\
';

  // ---------------------------------------------------------------- DOM

  var host = document.createElement('div');
  host.setAttribute('data-website-chatbot', '');
  var shadow = host.attachShadow({ mode: 'open' });
  var style = document.createElement('style');
  style.textContent = CSS;
  shadow.appendChild(style);

  var root = document.createElement('div');
  root.className = 'root';
  root.style.setProperty('--c', config.color);
  root.innerHTML =
    '<div class="panel" role="dialog" aria-modal="false" aria-label="' + escapeHtml(config.title) + '">' +
    '  <div class="head">' +
    '    <h2></h2>' +
    '    <button type="button" class="dbg" aria-pressed="false" aria-label="Show how answers are made" title="How this answer was made">i</button>' +
    '    <button type="button" class="close" aria-label="Close chat">&#x2715;</button>' +
    '  </div>' +
    '  <div class="msgs" role="log" aria-live="polite" aria-relevant="additions"></div>' +
    '  <form class="compose">' +
    '    <textarea rows="1" maxlength="500" placeholder="Ask a question about this website" aria-label="Your question"></textarea>' +
    '    <button type="submit" aria-label="Send">' +
    '      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4 20-7z"/></svg>' +
    '    </button>' +
    '  </form>' +
    '  <div class="foot">Proof of concept. Answers come from this website only.</div>' +
    '</div>' +
    '<button type="button" class="bubble" aria-label="Open chat" aria-expanded="false">' +
    '  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a8 8 0 0 1-8 8H7l-4 3V12a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8z"/></svg>' +
    '</button>';
  shadow.appendChild(root);

  var el = {
    panel: root.querySelector('.panel'),
    title: root.querySelector('h2'),
    dbg: root.querySelector('.dbg'),
    close: root.querySelector('.close'),
    msgs: root.querySelector('.msgs'),
    form: root.querySelector('.compose'),
    input: root.querySelector('textarea'),
    send: root.querySelector('.compose button'),
    bubble: root.querySelector('.bubble')
  };
  el.title.textContent = config.title;

  // ---------------------------------------------------------------- state

  var state = { site: config.site, session: null, busy: false, debug: config.debug, history: [] };

  function sessionKey() { return 'wcb_session_' + state.site; }
  function historyKey() { return 'wcb_history_' + state.site; }

  function loadSession() {
    var id = storageGet(sessionKey());
    if (!id) { id = uuid(); storageSet(sessionKey(), id); }
    state.session = id;
    try { state.history = JSON.parse(storageGet(historyKey()) || '[]'); } catch (e) { state.history = []; }
  }
  function saveHistory() {
    storageSet(historyKey(), JSON.stringify(state.history.slice(-30)));
  }

  function setDebug(on) {
    state.debug = !!on;
    el.dbg.setAttribute('aria-pressed', state.debug ? 'true' : 'false');
    el.msgs.querySelectorAll('.debug').forEach(function (d) { d.style.display = state.debug ? '' : 'none'; });
  }

  // ---------------------------------------------------------------- rendering

  function scrollDown() { el.msgs.scrollTop = el.msgs.scrollHeight; }

  function addUser(text) {
    var div = document.createElement('div');
    div.className = 'msg user';
    div.textContent = text;
    el.msgs.appendChild(div);
    scrollDown();
    return div;
  }

  function addBot() {
    var div = document.createElement('div');
    div.className = 'msg bot';
    div.innerHTML = '<div class="typing" aria-label="Assistant is typing"><span></span><span></span><span></span></div>';
    el.msgs.appendChild(div);
    scrollDown();
    return div;
  }

  function renderBot(div, text, sources, done) {
    var body = div.querySelector('.body');
    if (!body) {
      div.innerHTML = '<div class="body"></div>';
      body = div.querySelector('.body');
    }
    body.innerHTML = formatAnswer(text, sources);
    var old = div.querySelector('.sources'); if (old) old.remove();
    if (sources && sources.length) {
      var wrap = document.createElement('div');
      wrap.className = 'sources';
      sources.forEach(function (s) {
        var safe = safeUrl(s.url);
        if (!safe) return;
        var a = document.createElement('a');
        a.href = safe; a.target = '_blank'; a.rel = 'noopener noreferrer';
        a.innerHTML = '<span class="n">' + escapeHtml(String(s.number)) + '</span><span class="t"></span>';
        a.querySelector('.t').textContent = s.title || s.url;
        a.title = s.url;
        wrap.appendChild(a);
      });
      div.appendChild(wrap);
    }
    var oldDbg = div.querySelector('.debug'); if (oldDbg) oldDbg.remove();
    if (done) div.appendChild(renderDebug(done));
    scrollDown();
  }

  function renderDebug(done) {
    var d = document.createElement('details');
    d.className = 'debug';
    d.style.display = state.debug ? '' : 'none';
    var dbg = done.debug || {};
    var html = '<summary>How this answer was made</summary><dl>' +
      '<dt>Outcome</dt><dd>' + escapeHtml(done.outcome || '') + '</dd>' +
      '<dt>Condensed question</dt><dd></dd>' +
      '<dt>Rewritten for search</dt><dd></dd>' +
      '<dt>Retrieval</dt><dd>' + fmtMs(done.retrieval_ms) + '</dd>' +
      '<dt>First token</dt><dd>' + fmtMs(done.first_token_ms) + '</dd>' +
      '<dt>Total</dt><dd>' + fmtMs(done.total_ms) + '</dd>' +
      '<dt>Tokens</dt><dd>' + escapeHtml(String(done.tokens_in || 0)) + ' in, ' + escapeHtml(String(done.tokens_out || 0)) + ' out</dd>' +
      '<dt>Estimated cost</dt><dd>$' + escapeHtml(Number(done.cost_usd || 0).toFixed(6)) + '</dd>' +
      (done.model ? '<dt>Model</dt><dd>' + escapeHtml(done.model) + '</dd>' : '') +
      '</dl>';
    d.innerHTML = html;
    d.querySelectorAll('dd')[1].textContent = dbg.condensed_query || '';
    var rw = d.querySelectorAll('dd')[2];
    if (dbg.rewritten_query) { rw.textContent = dbg.rewritten_query; } else { rw.previousElementSibling.remove(); rw.remove(); }
    var chunks = dbg.chunks || [];
    if (!chunks.length) {
      var none = document.createElement('div');
      none.className = 'chunk';
      none.textContent = 'No passage cleared the similarity threshold, so the language model was not called.';
      d.appendChild(none);
    }
    chunks.forEach(function (c) {
      var div = document.createElement('div');
      div.className = 'chunk';
      var meta = document.createElement('div');
      meta.className = 'meta';
      var safe = safeUrl(c.url);
      meta.innerHTML = '[' + escapeHtml(String(c.number)) + '] similarity ' + escapeHtml(Number(c.similarity).toFixed(3)) + ' &middot; ';
      if (safe) {
        var a = document.createElement('a'); a.href = safe; a.target = '_blank'; a.rel = 'noopener noreferrer';
        a.textContent = (c.heading && c.heading !== c.title) ? (c.title + ' / ' + c.heading) : (c.title || c.url);
        meta.appendChild(a);
      }
      var p = document.createElement('div');
      p.textContent = c.preview || '';
      div.appendChild(meta); div.appendChild(p);
      d.appendChild(div);
    });
    return d;
  }

  function showError(div, message, retryText) {
    div.className = 'msg err';
    div.innerHTML = '';
    var p = document.createElement('div');
    p.textContent = message;
    div.appendChild(p);
    if (retryText) {
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'retry'; b.textContent = 'Try again';
      b.addEventListener('click', function () { div.remove(); send(retryText, true); });
      div.appendChild(b);
    }
    scrollDown();
  }

  function restoreHistory() {
    el.msgs.innerHTML = '';
    if (!state.history.length) {
      var hello = addBot();
      renderBot(hello, 'Hi! Ask me anything about this website and I will answer from its pages, with links to where I found it.', [], null);
      return;
    }
    state.history.forEach(function (m) {
      if (m.role === 'user') addUser(m.text);
      else renderBot(addBot(), m.text, m.sources || [], m.done || null);
    });
  }

  // ---------------------------------------------------------------- SSE

  function parseSSE(buffer, onEvent) {
    var parts = buffer.split('\n\n');
    var rest = parts.pop();
    parts.forEach(function (block) {
      var event = 'message', data = [];
      block.split('\n').forEach(function (line) {
        if (line.indexOf('event:') === 0) event = line.slice(6).trim();
        else if (line.indexOf('data:') === 0) data.push(line.slice(5).trim());
      });
      if (data.length) {
        try { onEvent(event, JSON.parse(data.join('\n'))); } catch (e) { /* ignore malformed frame */ }
      }
    });
    return rest;
  }

  function send(text, isRetry) {
    text = (text || '').trim();
    if (!text || state.busy) return;
    if (!state.site) {
      showError(addBot(), 'This chat is not connected to a website yet. Index one first.', null);
      return;
    }
    state.busy = true;
    el.send.disabled = true;
    if (!isRetry) {
      addUser(text);
      state.history.push({ role: 'user', text: text });
      saveHistory();
    } else {
      addUser(text);
    }
    var bot = addBot();
    var answer = '', sources = [], doneInfo = null, gotError = false;

    fetch(config.api + '/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ site_id: state.site, session_id: state.session, message: text })
    }).then(function (res) {
      if (!res.ok) {
        return res.json().catch(function () { return {}; }).then(function (j) {
          var msg = (j && j.detail) ? String(j.detail) : 'The chat service is not available right now.';
          if (res.status === 429 && /limit reached/i.test(msg)) { showError(bot, msg, null); }
          else if (res.status === 429) { showError(bot, msg, null); }
          else { showError(bot, msg, text); }
          gotError = true;
        });
      }
      var reader = res.body.getReader();
      var decoder = new TextDecoder();
      var buffer = '';
      function handle(event, data) {
        if (event === 'meta') { sources = data.sources || []; }
        else if (event === 'token') { answer += data.text || ''; renderBot(bot, answer, sources, null); }
        else if (event === 'done') { doneInfo = data; renderBot(bot, answer, sources, doneInfo); }
        else if (event === 'error') { gotError = true; showError(bot, data.message || 'Something went wrong.', text); }
      }
      function pump() {
        return reader.read().then(function (r) {
          if (r.done) { buffer = parseSSE(buffer + '\n\n', handle); return; }
          buffer += decoder.decode(r.value, { stream: true });
          buffer = parseSSE(buffer, handle);
          return pump();
        });
      }
      return pump();
    }).catch(function () {
      if (!gotError) showError(bot, 'Could not reach the chat service. Check your connection and try again.', text);
      gotError = true;
    }).then(function () {
      if (!gotError) {
        if (!answer) { showError(bot, 'No answer was returned. Please try again.', text); }
        else {
          state.history.push({ role: 'assistant', text: answer, sources: sources, done: doneInfo });
          saveHistory();
        }
      }
      state.busy = false;
      el.send.disabled = false;
      el.input.focus();
    });
  }

  // ---------------------------------------------------------------- open / close

  function open() {
    el.panel.classList.add('open');
    el.bubble.setAttribute('aria-expanded', 'true');
    el.bubble.setAttribute('aria-label', 'Close chat');
    setTimeout(function () { el.input.focus(); scrollDown(); }, 30);
  }
  function close() {
    el.panel.classList.remove('open');
    el.bubble.setAttribute('aria-expanded', 'false');
    el.bubble.setAttribute('aria-label', 'Open chat');
    el.bubble.focus();
  }
  function toggle() { if (el.panel.classList.contains('open')) close(); else open(); }

  function setSite(siteId, opts) {
    opts = opts || {};
    state.site = siteId || '';
    if (opts.reset && state.site) {
      storageSet(sessionKey(), uuid());
      storageSet(historyKey(), '[]');
    }
    loadSession();
    restoreHistory();
  }

  // ---------------------------------------------------------------- events

  el.bubble.addEventListener('click', toggle);
  el.close.addEventListener('click', close);
  el.dbg.addEventListener('click', function () { setDebug(!state.debug); });
  el.form.addEventListener('submit', function (e) {
    e.preventDefault();
    var text = el.input.value;
    el.input.value = '';
    el.input.style.height = '';
    send(text, false);
  });
  el.input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); el.form.requestSubmit ? el.form.requestSubmit() : el.form.dispatchEvent(new Event('submit', { cancelable: true })); }
  });
  el.input.addEventListener('input', function () {
    el.input.style.height = 'auto';
    el.input.style.height = Math.min(el.input.scrollHeight, 96) + 'px';
  });
  el.panel.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });

  // ---------------------------------------------------------------- boot

  function boot() {
    document.body.appendChild(host);
    setDebug(config.debug);
    setSite(config.site, {});
  }
  if (document.body) boot(); else document.addEventListener('DOMContentLoaded', boot);

  window.SiteChat = { open: open, close: close, toggle: toggle, setSite: setSite, setDebug: setDebug, config: config };
})();
