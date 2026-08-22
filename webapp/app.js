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
  const pending = addMessage('...', 'pending');
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
    addMessage(extractResult(data), 'agent');
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
