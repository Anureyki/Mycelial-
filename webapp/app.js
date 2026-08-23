const log = document.getElementById('log');
const form = document.getElementById('composer');
const promptInput = document.getElementById('promptInput');
const sendBtn = document.getElementById('sendBtn');
const settingsBtn = document.getElementById('settingsBtn');
const settingsPanel = document.getElementById('settingsPanel');
const serverUrlInput = document.getElementById('serverUrl');
const saveSettingsBtn = document.getElementById('saveSettings');
const chatTabBtn = document.getElementById('chatTabBtn');
const dashboardTabBtn = document.getElementById('dashboardTabBtn');
const dashboard = document.getElementById('dashboard');
const refreshDashboardBtn = document.getElementById('refreshDashboard');
const imageInput = document.getElementById('imageInput');
const attachBtn = document.getElementById('attachBtn');
const attachPreview = document.getElementById('attachPreview');
const attachName = document.getElementById('attachName');
const attachClear = document.getElementById('attachClear');

let pendingImages = []; // [{ base64, name }]

const STORAGE_KEY = 'mycelial.serverUrl';

function getServerUrl() {
  return localStorage.getItem(STORAGE_KEY) || '';
}

function setServerUrl(url) {
  localStorage.setItem(STORAGE_KEY, url.replace(/\/+$/, ''));
}

function addMessage(text, cls) {
  const el = document.createElement('div');
  el.className = `msg ${cls}`;
  el.textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

// Anansi crossing the mycelium while she looks something up. The three dots
// said "waiting"; this says who is waiting and what they are doing.
// Pure inline SVG so it needs no asset and works offline from the cached shell.
// --- Voice ------------------------------------------------------------------
// Two directions, both browser-native so nothing is sent anywhere and no key is
// needed: dictation instead of typing, and Anansi read aloud.
//
// This matters more than a convenience here. The grower does not want to sit
// typing readings; the same reason the sensors went in applies to the interface.
const micBtn = document.getElementById('micBtn');
const speakBtn = document.getElementById('speakBtn');
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let recog = null, listening = false;
let speakReplies = localStorage.getItem('mycelial.speak') === '1';

function markSpeakBtn() {
  if (!speakBtn) return;
  speakBtn.setAttribute('aria-pressed', speakReplies ? 'true' : 'false');
  speakBtn.classList.toggle('on', speakReplies);
  speakBtn.title = speakReplies ? 'Replies are read aloud - tap to mute'
                                : 'Read replies aloud';
}

function speak(text) {
  if (!speakReplies || !text || !('speechSynthesis' in window)) return;
  try {
    // Strip the ml/ppm shorthand so it is not read letter by letter.
    const said = String(text)
      .replace(/(\d)\s*ml\b/gi, '$1 millilitres')
      .replace(/\bppm\b/gi, 'P P M')
      .replace(/\bpH\b/g, 'p H')
      .replace(/\bDWC\b/gi, 'D W C')
      .slice(0, 1200);
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(said);
    u.rate = 1.0; u.pitch = 1.0;
    window.speechSynthesis.speak(u);
  } catch (e) { /* speaking is never worth breaking the page for */ }
}

if (speakBtn) {
  markSpeakBtn();
  speakBtn.addEventListener('click', () => {
    if (!('speechSynthesis' in window)) {
      addMessage('This browser has no speech synthesis.', 'error');
      return;
    }
    speakReplies = !speakReplies;
    localStorage.setItem('mycelial.speak', speakReplies ? '1' : '0');
    markSpeakBtn();
    // iOS only permits speech started from a user gesture, so the confirmation
    // doubles as the unlock - and proves to the user that it works.
    if (speakReplies) speak('Voice on. Anansi will read replies aloud.');
  });
}

// iOS Safari has never shipped Web Speech RECOGNITION - Apple implemented the
// synthesis half only, so `webkitSpeechRecognition` is absent on iPhone no
// matter the version. The button should say so rather than look available and
// then fail, and iOS already has better dictation built into the keyboard.
if (micBtn && !SR) {
  micBtn.classList.add('unavailable');
  micBtn.title = 'Dictation: use the microphone key on your keyboard';
  micBtn.setAttribute('aria-label', 'Dictation unavailable - use the keyboard microphone');
}

if (micBtn) {
  micBtn.addEventListener('click', () => {
    if (!SR) {
      // Not an error - a redirection to the thing that does work here.
      addMessage('Dictation is not available in this browser - iOS never shipped it. '
               + 'Tap the microphone key on your iPhone keyboard instead and talk; '
               + 'it types into the box and you can check it before sending.', 'agent');
      promptInput.focus();
      return;
    }
    if (listening) { try { recog.stop(); } catch (e) {} return; }
    recog = new SR();
    recog.lang = 'en-US';
    recog.interimResults = true;
    recog.continuous = false;
    let finalText = '';
    recog.onstart = () => { listening = true; micBtn.classList.add('on'); };
    recog.onresult = (ev) => {
      let interim = '';
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const t = ev.results[i][0].transcript;
        if (ev.results[i].isFinal) finalText += t; else interim += t;
      }
      promptInput.value = (finalText + interim).trim();
    };
    recog.onerror = (ev) => {
      addMessage(`Microphone: ${ev.error}. Check the browser has mic permission.`, 'error');
    };
    recog.onend = () => {
      listening = false; micBtn.classList.remove('on');
      // Do NOT auto-send. A misheard reading is worse than a retyped one, and
      // the grower gets to see what it heard before it becomes a record.
      promptInput.focus();
    };
    try { recog.start(); } catch (e) { addMessage(`Could not start the mic: ${e.message}`, 'error'); }
  });
}

function addWaiting() {
  const el = document.createElement('div');
  el.className = 'msg pending';
  el.innerHTML = `
<svg class="anansi" viewBox="0 0 160 44" role="img" aria-label="Anansi is looking...">
  <g class="shrooms" fill="currentColor" opacity="0.35">
    <g transform="translate(14,34)"><rect x="-1.5" y="-7" width="3" height="7" rx="1"/>
      <path d="M-7 -7 a7 5 0 0 1 14 0 z"/></g>
    <g transform="translate(52,34)"><rect x="-1" y="-5" width="2" height="5" rx="1"/>
      <path d="M-5 -5 a5 3.5 0 0 1 10 0 z"/></g>
    <g transform="translate(96,34)"><rect x="-1.5" y="-8" width="3" height="8" rx="1"/>
      <path d="M-8 -8 a8 5.5 0 0 1 16 0 z"/></g>
    <g transform="translate(138,34)"><rect x="-1" y="-6" width="2" height="6" rx="1"/>
      <path d="M-6 -6 a6 4 0 0 1 12 0 z"/></g>
  </g>
  <line x1="0" y1="34" x2="160" y2="34" stroke="currentColor" stroke-width="1" opacity="0.25"/>
  <g class="spider" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
    <g class="legs">
      <path d="M-6 0 l-5 -4 l-3 4"/><path d="M-6 2 l-6 1 l-3 4"/>
      <path d="M6 0 l5 -4 l3 4"/><path d="M6 2 l6 1 l3 4"/>
    </g>
    <ellipse cx="0" cy="0" rx="5.5" ry="4.5" fill="currentColor" stroke="none"/>
    <circle cx="6.5" cy="-1" r="2.6" fill="currentColor" stroke="none"/>
  </g>
</svg><span class="waiting-text">Anansi is looking</span>`;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

function extractResult(payload) {
  let node = payload;
  for (let i = 0; i < 6 && node && typeof node === 'object'; i++) {
    if (typeof node.result === 'string') return node.result;
    if (node.result && typeof node.result === 'object') { node = node.result; continue; }
    break;
  }
  return JSON.stringify(payload);
}

async function sendPrompt(text, images) {
  const serverUrl = getServerUrl();
  if (!serverUrl) {
    addMessage('No server URL set. Open settings (gear icon) and set your Anansi server URL first.', 'error');
    openSettings();
    return;
  }

  const n = (images || []).length;
  const label = n === 1 ? `[photo: ${images[0].name}]` : `[${n} photos attached]`;
  addMessage(n ? `${text || '(photos)'} \n${label}` : text, 'user');
  const pending = addWaiting();
  sendBtn.disabled = true;

  try {
    const body = n
      ? { task: 'process_request', args: [JSON.stringify({
            prompt: text || (n > 1 ? 'Uploaded plant photos' : 'Uploaded a plant photo'),
            metadata: {
              images: images.map((im) => ({ data: im.base64, name: im.name })),
              // Single-image fields kept so an older server still works.
              image_base64: images[0].base64,
              image_name: images[0].name,
            },
          })] }
      : { task: 'process_request', args: [text] };
    const res = await fetch(`${serverUrl}/execute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    pending.remove();
    const replyText = extractResult(data);
    addMessage(replyText, 'agent');
    speak(replyText);
  } catch (err) {
    pending.remove();
    addMessage(`Request failed: ${err.message}`, 'error');
  } finally {
    sendBtn.disabled = false;
  }
}

async function fetchNarration(promptText) {
  const serverUrl = getServerUrl();
  if (!serverUrl) return 'No server URL set - open settings first.';
  try {
    const res = await fetch(`${serverUrl}/execute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task: 'process_request', args: [promptText] }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return extractResult(data);
  } catch (err) {
    return `Couldn't load this right now (${err.message}).`;
  }
}

async function refreshDashboard() {
  const cards = [
    { id: 'systemCard', prompt: 'system status' },
    { id: 'growCard', prompt: 'how is my plant' },
    { id: 'progressCard', prompt: 'catch me up on progress' },
    { id: 'decisionsCard', prompt: 'what needs my approval' },
  ];
  for (const { id, prompt } of cards) {
    const body = document.querySelector(`#${id} .card-body`);
    if (body) body.textContent = 'Loading...';
  }
  // Sequential, not parallel - these route through the same shared local
  // inference backend, so firing all three at once would just make them
  // queue behind each other anyway.
  for (const { id, prompt } of cards) {
    const body = document.querySelector(`#${id} .card-body`);
    const text = await fetchNarration(prompt);
    if (body) body.textContent = text;
  }
}

function showChat() {
  chatTabBtn.classList.add('active');
  dashboardTabBtn.classList.remove('active');
  log.classList.remove('hidden');
  dashboard.classList.add('hidden');
  form.classList.remove('hidden');
}

function showDashboard() {
  dashboardTabBtn.classList.add('active');
  chatTabBtn.classList.remove('active');
  dashboard.classList.remove('hidden');
  log.classList.add('hidden');
  form.classList.add('hidden');
  refreshDashboard();
}

chatTabBtn.addEventListener('click', showChat);
dashboardTabBtn.addEventListener('click', showDashboard);
refreshDashboardBtn.addEventListener('click', refreshDashboard);

function openSettings() {
  serverUrlInput.value = getServerUrl();
  settingsPanel.classList.remove('hidden');
}

settingsBtn.addEventListener('click', () => {
  settingsPanel.classList.toggle('hidden');
  if (!settingsPanel.classList.contains('hidden')) {
    serverUrlInput.value = getServerUrl();
  }
});

saveSettingsBtn.addEventListener('click', () => {
  const url = serverUrlInput.value.trim();
  if (!url) return;
  setServerUrl(url);
  settingsPanel.classList.add('hidden');
  addMessage(`Server set to ${url}`, 'agent');
});

attachBtn.addEventListener('click', () => imageInput.click());

imageInput.addEventListener('change', () => {
  const files = Array.from(imageInput.files || []);
  if (!files.length) return;
  let pending = files.length;
  files.forEach((file) => {
    const reader = new FileReader();
    reader.onload = () => {
      const base64 = String(reader.result).split(',')[1] || '';
      pendingImages.push({ base64, name: file.name || 'photo.jpg' });
      if (--pending === 0) refreshAttachPreview();
    };
    reader.onerror = () => {
      addMessage(`Couldn't read ${file.name || 'a photo'} - skipped.`, 'error');
      if (--pending === 0) refreshAttachPreview();
    };
    reader.readAsDataURL(file);
  });
});

function refreshAttachPreview() {
  const n = pendingImages.length;
  if (!n) { attachPreview.classList.add('hidden'); return; }
  attachName.textContent = n === 1
    ? pendingImages[0].name
    : `${n} photos attached`;
  attachPreview.classList.remove('hidden');
}

attachClear.addEventListener('click', () => {
  pendingImages = [];
  imageInput.value = '';
  attachPreview.classList.add('hidden');
});

form.addEventListener('submit', (e) => {
  e.preventDefault();
  const text = promptInput.value.trim();
  if (!text && !pendingImages.length) return;
  const images = pendingImages;
  promptInput.value = '';
  promptInput.style.height = 'auto';
  pendingImages = [];
  imageInput.value = '';
  attachPreview.classList.add('hidden');
  sendPrompt(text, images);
});

promptInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

promptInput.addEventListener('input', () => {
  promptInput.style.height = 'auto';
  promptInput.style.height = `${promptInput.scrollHeight}px`;
});

if (!getServerUrl()) {
  openSettings();
}

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('service-worker.js').catch(() => {});
  });
}
