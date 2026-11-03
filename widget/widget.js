                                                                    
             
                                                                            
                                                    
  
                                                        
                                                                                                         
                                                                                                      ;
              
                                                                                            
                                                                                                  
  

(() => {
  const script = document.currentScript                            ;
  const key = script?.dataset.chatbotKey;
  if (!script || !key) return;
  const base = new URL(script.src).origin + '/api/v1/chat/' + encodeURIComponent(key) + '/widget';
  const storageKey = 'chatbot-widget:' + base;
  const defaults        = {
    name: 'Assistant', color: '#064e3b', position: 'right', theme: 'auto',
    icon: 'chat', greeting: 'Hi! How can we help?', show_greeting: true, avatar_url: '',
  };
  let theme = defaults;
  const fresh = ()        => ({ conversation: null, entries: [], pending: null, after: 0 });
  let state = fresh();
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved) state = JSON.parse(saved)         ;
  } catch { /* Storage may be blocked by the host browser. */ }
  let busy = false;
  let polling = false;
  const host = document.createElement('div');
  const root = host.attachShadow({ mode: 'open' });
  const style = document.createElement('style');
  style.textContent = `
    :host{all:initial;--accent:#064e3b;--accent-text:white;--bg:white;--text:#172221;--line:#d5dcda}
    *{box-sizing:border-box}button,input{font:inherit}button{cursor:pointer}button:disabled{cursor:default;opacity:.55}
    button:focus-visible,input:focus-visible,summary:focus-visible{outline:2px solid currentColor;outline-offset:2px}
    [hidden]{display:none!important}.launcher,.bubble,.panel{position:fixed;right:20px;z-index:2147483646;
      font:14px/1.45 system-ui,sans-serif}.left{right:auto;left:20px}
    .launcher{bottom:20px;border:0;border-radius:28px;min-height:48px;min-width:48px;padding:0 16px;
      background:var(--accent);color:var(--accent-text);box-shadow:0 4px 20px #0003}
    .bubble{bottom:78px;max-width:min(260px,calc(100vw - 40px));padding:10px;border-radius:12px;
      background:var(--bg);color:var(--text);border:1px solid var(--line)}
    .panel{bottom:78px;width:min(380px,calc(100vw - 40px));height:min(600px,calc(100dvh - 94px));
      border:1px solid var(--line);border-radius:16px;display:flex;flex-direction:column;
      overflow:hidden;background:var(--bg);color:var(--text);box-shadow:0 8px 30px #0003}
    header{display:flex;align-items:center;gap:8px;padding:12px;background:var(--accent);color:var(--accent-text)}
    header div{flex:1;min-width:0;overflow-wrap:anywhere}header small{display:block}header img{width:36px;
      height:36px;object-fit:cover;border-radius:50%}
    button{background:var(--bg);color:var(--text);border:1px solid var(--line);border-radius:8px;min-height:40px;
      padding:5px 9px}header button{background:transparent;color:inherit;border:1px solid currentColor}
    .log{flex:1;overflow:auto;overscroll-behavior:contain;padding:12px}.message{margin:0 0 12px;overflow-wrap:anywhere}
    .message p{margin:4px 0;white-space:pre-wrap}.tools{display:flex;gap:5px;flex-wrap:wrap;padding:8px}
    .sources{border:1px solid var(--line);border-radius:8px;padding:3px 8px;margin:5px 0}
    .sources summary{cursor:pointer}.vote[aria-pressed=true]{outline:2px solid var(--accent)}
    form{display:flex;gap:6px;padding:8px;border-top:1px solid var(--line)}
    input{min-width:0;flex:1;padding:8px;background:var(--bg);color:var(--text);border:1px solid var(--line);border-radius:8px}
    .status{padding:0 8px}.powered{font-size:11px;text-align:center;padding:3px}
    @media(max-width:479px){.panel,.panel.left{inset:0;width:100%;height:100dvh;border:0;border-radius:0;
      z-index:2147483647}form{padding-bottom:max(8px,env(safe-area-inset-bottom))}}
  `;
  root.append(style);

  function element                                       (tag   , text = '', className = '') {
    const node = document.createElement(tag);
    node.textContent = text;
    node.className = className;
    return node;
  }
  function button(text        , action            , className = '') {
    const node = element('button', text, className);
    node.type = 'button';
    node.addEventListener('click', action);
    return node;
  }
  const launcher = button('Chat', open, 'launcher');
  launcher.setAttribute('aria-label', 'Open chat');
  launcher.setAttribute('aria-expanded', 'false');
  const bubble = element('div', '', 'bubble');
  bubble.hidden = true;
  const panel = element('section', '', 'panel');
  panel.hidden = true;
  panel.setAttribute('aria-label', 'Website chat');
  const header = element('header');
  const avatar = element('img');
  avatar.alt = '';
  avatar.hidden = true;
  avatar.referrerPolicy = 'no-referrer';
  const title = element('div');
  const name = element('strong', theme.name);
  title.append(name, element('small', 'Usually replies instantly'));
  header.append(avatar, title, button('Close', close));
  const log = element('div', '', 'log');
  log.setAttribute('role', 'log');
  log.setAttribute('aria-label', 'Chat messages');
  const status = element('div', '', 'status');
  status.setAttribute('role', 'status');
  const tools = element('div', '', 'tools');
  const handoff = button('Talk to a person', () => queue('Talk to a person', true));
  const retry = button('Retry', () => void deliver());
  retry.hidden = true;
  const reset = button('Start a new chat', () => {
    state = fresh();
    status.textContent = '';
    retry.hidden = true;
    persist();
    render();
    input.focus();
  });
  tools.append(handoff, reset, retry);
  const form = element('form');
  const input = element('input');
  input.placeholder = 'Ask a question';
  input.setAttribute('aria-label', 'Ask a question');
  input.required = true;
  input.maxLength = 4000;
  const send = element('button', 'Send');
  send.type = 'submit';
  form.append(input, send);
  panel.append(header, log, status, tools, form, element('div', 'Powered by Chatbot', 'powered'));
  root.append(launcher, bubble, panel);

  function persist() {
    try { localStorage.setItem(storageKey, JSON.stringify({ ...state, entries: state.entries.slice(-60) })); }
    catch { /* The open chat still works without browser storage. */ }
  }
  function append(entry       ) {
    state.entries.push(entry);
    state.entries = state.entries.slice(-60);
    persist();
    render();
  }
  function render() {
    log.replaceChildren();
    for (const entry of state.entries) {
      const row = element('article', '', 'message');
      row.append(element('strong', entry.who), element('p', entry.text));
      const turn = entry.turn;
      if (turn?.sources.length) {
        const sources = element('details', '', 'sources');
        sources.append(element('summary', 'Sources (' + turn.sources.length + ')'));
        for (const source of turn.sources) {
          sources.append(element('p', [source.filename, source.heading, source.page == null ? '' : 'page ' + source.page]
            .filter(Boolean).join(' · ')));
        }
        row.append(sources);
      }
      if (turn) {
        for (const vote of ['up', 'down']         ) {
          const control = button(vote === 'up' ? '👍' : '👎', () => void rate(turn, vote), 'vote');
          control.setAttribute('aria-label', vote === 'up' ? 'Helpful answer' : 'Unhelpful answer');
          control.setAttribute('aria-pressed', String(turn.feedback === vote));
          row.append(control);
        }
      }
      log.append(row);
    }
    log.scrollTop = log.scrollHeight;
    send.disabled = handoff.disabled = !!state.pending;
    reset.disabled = busy;
  }
  function open() {
    panel.hidden = false;
    launcher.hidden = bubble.hidden = true;
    launcher.setAttribute('aria-expanded', 'true');
    input.focus();
    if (state.pending) void deliver();
    void readAgentReplies();
  }
  function close() {
    panel.hidden = true;
    launcher.hidden = false;
    launcher.setAttribute('aria-expanded', 'false');
    launcher.focus();
  }
  panel.addEventListener('keydown', event => { if (event.key === 'Escape') close(); });
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (input.value.trim() && !state.pending) {
      queue(input.value.trim());
      input.value = '';
    }
  });

  async function request   (path        , data         )             {
    const result = await fetch(base + path, {
      method: data ? 'POST' : 'GET', credentials: 'omit', cache: 'no-store',
      signal: AbortSignal.timeout(15000),
      ...(data ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) } : {}),
    });
    if (!result.ok) throw new Error(result.status === 429 ? 'Please wait a minute before retrying.' :
      'Could not reach the chat service. Try again.');
    return result.json()              ;
  }
  function queue(message        , handoffRequest = false) {
    if (state.pending) return;
    state.pending = { request_id: crypto.randomUUID(), conversation_id: state.conversation,
      message, handoff: handoffRequest };
    append({ who: 'You', text: message });
    void deliver();
  }
  async function awaitTurn(turn      ) {
    if (turn.state === 'complete' || turn.state === 'failed') return turn;
    const path = '/turns/' + encodeURIComponent(turn.id);
    if ('EventSource' in window) {
      const streamed = await new Promise             (resolve => {
        const source = new EventSource(base + path + '/events');
        const settle = (result             ) => { source.close(); clearTimeout(timer); resolve(result); };
        const timer = setTimeout(() => settle(null), 35000);
        source.addEventListener('result', event => {
          try { settle(JSON.parse(event.data)        ); } catch { settle(null); }
        });
        source.onerror = () => settle(null);
      });
      if (streamed) return streamed;
    }
    for (let attempt = 0; attempt < 30; attempt++) {
      const current = await request      (path);
      if (current.state === 'complete' || current.state === 'failed') return current;
      await new Promise(resolve => setTimeout(resolve, 2000));
    }
    throw new Error('Your message is still processing. Retry to check for a reply.');
  }
  async function deliver() {
    if (busy || !state.pending) return;
    if (!navigator.onLine) {
      status.textContent = 'You’re offline. We’ll send your message when you reconnect.';
      retry.hidden = false;
      return;
    }
    busy = true;
    retry.hidden = true;
    status.textContent = theme.name + ' is typing…';
    render();
    try {
      const queued = await request      ('/turns', state.pending);
      state.conversation = queued.conversation_id;
      persist();
      const turn = await awaitTurn(queued);
      if (turn.state === 'failed') {
        state.pending = { ...state.pending, conversation_id: state.conversation, request_id: crypto.randomUUID() };
        persist();
        throw new Error('We couldn’t answer that message. Please try again.');
      }
      state.pending = null;
      append({ who: theme.name, text: turn.answer ?? '', turn });
      status.textContent = '';
    } catch (failure) {
      status.textContent = !navigator.onLine ? 'You’re offline. Your message is saved.'
        : failure instanceof Error ? failure.message : 'Could not send your message.';
      retry.hidden = false;
    } finally {
      busy = false;
      render();
    }
  }
  async function rate(turn      , vote               ) {
    try {
      const result = await request                             ('/turns/' + turn.id + '/feedback', { vote });
      turn.feedback = result.feedback;
      persist();
      render();
    } catch { status.textContent = 'Could not save your feedback.'; }
  }
  async function readAgentReplies() {
    if (panel.hidden || !state.conversation || polling || !navigator.onLine) return;
    polling = true;
    const conversation = state.conversation;
    try {
      const rows = await request                                ('/conversations/' + conversation +
        '/replies?after=' + state.after);
      if (state.conversation !== conversation) return;
      for (const row of rows) {
        state.after = row.id;
        append({ who: 'Team', text: row.text });
      }
    } catch { /* A later poll can recover missed team replies. */ }
    finally { polling = false; }
  }

  function applyTheme(config       ) {
    theme = config;
    name.textContent = theme.name;
    const rgb = theme.color.match(/[0-9a-f]{2}/gi)?.map(value => parseInt(value, 16) / 255) ?? [0, 0, 0];
    const brightness = rgb.map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
    const light = .2126 * brightness[0] + .7152 * brightness[1] + .0722 * brightness[2];
    host.style.setProperty('--accent', theme.color);
    host.style.setProperty('--accent-text', light > .179 ? '#000' : '#fff');
    const dark = theme.theme === 'dark' || (theme.theme === 'auto' && matchMedia('(prefers-color-scheme: dark)').matches);
    host.style.setProperty('--bg', dark ? '#182523' : '#fff');
    host.style.setProperty('--text', dark ? '#f4f7f6' : '#172221');
    host.style.setProperty('--line', dark ? '#50615e' : '#d5dcda');
    for (const node of [launcher, panel, bubble]) node.classList.toggle('left', theme.position === 'left');
    launcher.textContent = { chat: 'Chat', sparkle: '✦', help: '?' }[theme.icon];
    if (theme.avatar_url) { avatar.src = theme.avatar_url; avatar.hidden = false; }
    if (!theme.show_greeting || !theme.greeting || !panel.hidden) return;
    try {
      if (localStorage.getItem(storageKey + ':greeting')) return;
      localStorage.setItem(storageKey + ':greeting', '1');
    } catch { /* Show once per page when storage is unavailable. */ }
    bubble.textContent = theme.greeting;
    bubble.hidden = false;
  }

  function mount() {
    document.body.append(host);
    render();
    void request       ('/config').then(applyTheme).catch(() => applyTheme(defaults));
    window.addEventListener('online', () => { if (state.pending) void deliver(); });
    window.setInterval(() => void readAgentReplies(), 4000);
  }
  if (document.body) mount();
  else document.addEventListener('DOMContentLoaded', mount, { once: true });
})();
