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
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved) return saved;
  // Served over TLS from the reverse proxy, the API is on the same origin, so
  // "" resolves /execute relatively and there is nothing to configure on the
  // phone. Opened from file:// there is no origin to borrow and the settings
  // panel is still needed.
  if (location.protocol === 'http:' || location.protocol === 'https:') return '';
  return null;
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
  if (serverUrl === null) {
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
  if (serverUrl === null) return 'No server URL set - open settings first.';
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

function ago(iso) {
  if (!iso) return 'never';
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (!isFinite(mins)) return '?';
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const h = Math.round(mins / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

function renderRows(body, rows) {
  body.textContent = '';
  const dl = document.createElement('dl');
  dl.className = 'rows';
  for (const [k, v] of rows) {
    const dt = document.createElement('dt');
    dt.textContent = k;
    const dd = document.createElement('dd');
    dd.textContent = v;
    dl.append(dt, dd);
  }
  body.append(dl);
}

// The Grow card asks for STATE and renders fields. It used to ask "how is my
// plant" down the narration path, which answered the question - returning an
// argument about feed strength while the grower wanted the numbers - and
// carried no timestamp, so output from an old conversation looked current.
async function renderGrowCard(body) {
  const d = unwrap(await callTask('grow_snapshot', {}), 'last_reading');
  if (!d || d.error) { body.textContent = d && d.error ? d.error : 'No grow data.'; return; }
  const r = d.last_reading || {};
  const res = d.reservoir || {};
  const rows = [
    ['Plant', `${d.strain || 'unknown'} · ${d.stage || 'unknown'}${d.day != null ? ` · day ${d.day}` : ''}`],
    ['Last reading', r.at ? ago(r.at) : 'never'],
    ['ppm', r.ppm != null
      ? String(r.ppm) + (r.derived_unit === 'ppm' ? ' (derived)' : '') : '—'],
    // EC beside ppm, and a note when one of them was computed rather than read.
    ['EC', r.ec != null
      ? `${r.ec} mS/cm` + (r.derived_unit === 'ec' ? ' (derived)' : '') : '—'],
    ['pH', r.ph != null ? String(r.ph) : '—'],
    ['Temp', r.temp_c != null ? `${r.temp_c} C` : '—'],
    // Volume says where it came from. A carried-forward level and a measured
    // one are different kinds of number and every dose is computed from it.
    ['Volume', r.volume_liters != null
      ? `${r.volume_liters} L (${r.volume_source || 'unknown'})` : '—'],
    ['Reservoir', res.liters != null
      ? `${res.liters} L of ${res.capacity_liters != null ? res.capacity_liters + ' L' : '?'}` : '—'],
    // Grams, with the arithmetic shown inline so it cannot be misread as a
    // concentration sitting next to the ppm row.
    ['Nutrient in it', d.nutrient_in_solution_g != null
      ? `${d.nutrient_in_solution_g} g` + (d.nutrient_basis ? ` (${d.nutrient_basis})` : '')
      : '—'],
  ];
  // The CHANGE is the finding; the level on its own is just a number. ppm
  // moves when water alone moves, so only mass says whether the plant fed.
  if (d.nutrient_change) {
    const c = d.nutrient_change;
    const sign = c.delta_g > 0 ? '+' : '';
    rows.push(['Change', `${sign}${c.delta_g} g`
      + (c.hours ? ` over ${c.hours}h` : '') + ` — ${c.meaning}`]);
  }
  // The reading schedule, at the top where it is acted on. A "due" state is
  // the one thing on this card that asks the grower to do something.
  const rd = d.reading_due;
  if (rd && rd.status) {
    const banner = document.createElement('p');
    banner.className = `due due-${rd.status}`;
    const when = rd.status === 'not_yet'
      ? `Next reading ${new Date(rd.due_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`
      : rd.status === 'due_soon' ? `Reading due in ${Math.round(rd.hours_until_due)}h`
      : rd.status === 'due_now' ? 'Reading due now'
      : 'Reading OVERDUE';
    banner.textContent = `${when} · every ${rd.recommended_days}d`;
    body.append(banner);
  }
  renderRows(body, rows);
  // An open problem is the reason to look at the card at all, so it is shown
  // rather than left to be inferred from the numbers. It renders the STATE -
  // what was seen, how many explanations are still live, what is being done -
  // and never a single named cause, because the differential exists precisely
  // because no cause is established. Naming one here would undo that in the
  // one place actually read.
  if (d.open_concern) {
    const c = d.open_concern;
    const box = document.createElement('div');
    box.className = 'concern';

    const head = document.createElement('p');
    head.className = 'concern-head';
    head.textContent = `Open concern · ${ago(c.at)}`;
    box.append(head);

    const what = document.createElement('p');
    what.className = 'concern-what';
    what.textContent = c.what || '(no description)';
    box.append(what);

    const rows = [];
    if (c.source === 'differential') {
      if (c.live_count) {
        const names = (c.front_runners || []).map(n => n.replace(/_/g, ' '));
        rows.push(['Explanations',
          `${c.live_count} live, none confirmed` + (names.length ? ` · leading: ${names.join(', ')}` : '')]);
      }
      if (c.decision) {
        rows.push(['Doing', c.decision === 'hold'
          ? 'holding — waiting on the next observation'
          : (c.changes && c.changes.length ? c.changes.join(', ') : c.decision)]);
      }
      if (c.watch_for) rows.push(['Watch for', c.watch_for]);
      if (c.reassess_after) rows.push(['Reassess', c.reassess_after.slice(0, 10)]);
    } else {
      if (c.action) rows.push(['Suggested', c.action]);
      if (c.confidence) rows.push(['Confidence', c.confidence]);
    }
    if (rows.length) {
      const dl = document.createElement('dl');
      dl.className = 'rows concern-rows';
      for (const [k, v] of rows) {
        const dt = document.createElement('dt'); dt.textContent = k;
        const dd = document.createElement('dd'); dd.textContent = v;
        dl.append(dt, dd);
      }
      box.append(dl);
    }
    body.append(box);
  } else {
    const p = document.createElement('p');
    p.className = 'muted';
    p.textContent = 'No open concern recorded.';
    body.append(p);
  }
}

// Progress answers two questions the grower kept having to hold in their head:
// where the roadmap stands, and what the SYSTEM has changed lately. It used to
// be a narrated paragraph, and then a changelog list dominated by domain
// bugfixes - real work, but not an answer to how the system is evolving.
async function renderProgressCard(body) {
  body.textContent = '';

  const ph = unwrap(await callTask('phase_status', {}), 'phases');
  if (ph && ph.phases) {
    const done = ph.phases.filter(p => /^\d+$/.test(p.number) && p.state === 'done').length;
    const hdr = document.createElement('p');
    hdr.className = 'phase-hdr';
    hdr.textContent = `Phase ${done}/${ph.total_numbered} complete`
      + (ph.next ? ` · next: ${ph.next.number}. ${ph.next.name}` : '');
    body.append(hdr);

    const track = document.createElement('ol');
    track.className = 'phases';
    for (const p of ph.phases) {
      if (!/^\d+$/.test(p.number)) continue;
      const li = document.createElement('li');
      li.className = `ph-${p.state}`;
      li.textContent = `${p.number}. ${p.name}`;
      track.append(li);
    }
    body.append(track);

    // Two places in one file said different things about phase 6 and the
    // stale one would have had this work repeated. Surfaced, never resolved
    // silently.
    if (ph.table_section_conflicts && ph.table_section_conflicts.length) {
      const w = document.createElement('p');
      w.className = 'concern';
      w.textContent = 'Roadmap disagrees with itself: '
        + ph.table_section_conflicts
            .map(c => `phase ${c.number} (table: ${c.table_says}, section: ${c.section_says})`)
            .join('; ');
      body.append(w);
    }
  }

  const d = unwrap(await callTask('recent_changes', { limit: 8 }), 'entries');
  if (!d || d.error || !d.entries) {
    const p = document.createElement('p');
    p.className = 'muted';
    p.textContent = d && d.error ? d.error : 'No change history.';
    body.append(p);
    return;
  }
  const h = document.createElement('p');
  h.className = 'phase-hdr';
  h.textContent = 'Recent system changes';
  body.append(h);
  const ul = document.createElement('ul');
  ul.className = 'changes';
  for (const e of d.entries) {
    const li = document.createElement('li');
    const t = document.createElement('time');
    t.textContent = e.date;
    // "docs" rides along on nearly every commit because the changelog is
    // updated with the change, so it carries no signal and is dropped.
    const scopes = (e.scopes || []).filter(x => x !== 'docs' && x !== 'other');
    li.append(t);
    if (scopes.length) {
      const sp = document.createElement('span');
      sp.className = 'scope';
      sp.textContent = scopes.join(' ');
      li.append(sp);
    }
    li.append(document.createTextNode(' ' + e.headline));
    ul.append(li);
  }
  body.append(ul);
  if (d.domain_only_omitted) {
    const p = document.createElement('p');
    p.className = 'muted';
    p.textContent = `${d.domain_only_omitted} domain-only commits not shown (agent bugfixes and grow work).`;
    body.append(p);
  }
}

async function refreshDashboard() {
  const narrated = [
    { id: 'systemCard', prompt: 'system status' },
    { id: 'decisionsCard', prompt: 'what needs my approval' },
  ];
  const structured = [
    { id: 'growCard', render: renderGrowCard },
    { id: 'progressCard', render: renderProgressCard },
  ];
  for (const { id } of [...narrated, ...structured]) {
    const body = document.querySelector(`#${id} .card-body`);
    if (body) body.textContent = 'Loading...';
  }
  // Structured cards first: they are a single memory read each and land
  // immediately, so the dashboard is useful before the narrated cards return.
  for (const { id, render } of structured) {
    const body = document.querySelector(`#${id} .card-body`);
    if (body) { try { await render(body); } catch (e) { body.textContent = `Couldn't load (${e.message}).`; } }
  }
  // Sequential, not parallel - these route through the same shared local
  // inference backend, so firing them at once would just make them queue.
  for (const { id, prompt } of narrated) {
    const body = document.querySelector(`#${id} .card-body`);
    const text = await fetchNarration(prompt);
    if (body) body.textContent = text;
  }
}

// --- Training review ---------------------------------------------------------
// The gate the sourcing loop never had. Candidates were being proposed into a
// queue with no way to judge them, so 3 spider-mite images sat awaiting review
// for days while every label stayed empty. Search proposes; this is where a
// human disposes.
const trainingTabBtn = document.getElementById('trainingTabBtn');
const training = document.getElementById('training');
const candidateList = document.getElementById('candidateList');
const questCard = document.getElementById('questCard');

async function callTask(task, args) {
  const base = getServerUrl();
  if (base === null) return { error: 'No server URL set' };
  const res = await fetch(`${base}/execute`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task, args: args || {} }),
  });
  if (!res.ok) return { error: `HTTP ${res.status}` };
  return res.json();
}

function unwrap(d, key) {
  let n = 0;
  while (d && typeof d === 'object' && !(key in d) && 'result' in d && n++ < 6) d = d.result;
  return d;
}

async function refreshTraining() {
  questCard.querySelector('.card-body').textContent = 'Loading...';
  candidateList.textContent = '';
  const q = unwrap(await callTask('training_quest_status'), 'per_label');
  if (q && q.per_label) {
    const short = q.per_label.filter(p => !p.complete)
      .map(p => `${p.label} ${p.have}/${p.have + p.need}`).join(' · ');
    questCard.querySelector('.card-body').textContent =
      `Level ${q.level} · ${q.labels_complete}/${q.labels_total} labels complete · ${q.overall_percent}%\n${short}`;
  } else {
    questCard.querySelector('.card-body').textContent = 'Campaign status unavailable.';
  }

  let items = unwrap(await callTask('training_candidates'), 'length');
  if (!Array.isArray(items)) items = (items && items.candidates) || [];
  if (!items.length) {
    candidateList.textContent = 'Nothing waiting for review.';
    return;
  }
  for (const c of items) {
    const row = document.createElement('div');
    row.className = 'card';
    const img = document.createElement('img');
    img.src = c.image_url || '';
    img.alt = c.label || 'candidate';
    img.loading = 'lazy';
    img.style.maxWidth = '100%';
    img.style.borderRadius = '6px';
    // A broken image must not look like a valid one to accept.
    img.onerror = () => { img.replaceWith(Object.assign(document.createElement('p'),
      { className: 'hint', textContent: 'Image would not load — reject this one.' })); };
    const meta = document.createElement('p');
    meta.className = 'hint';
    meta.textContent = `${c.label} — ${c.source_title || 'untitled'}`;
    const src = document.createElement('a');
    src.href = c.source_url || '#'; src.target = '_blank'; src.rel = 'noreferrer noopener';
    src.textContent = 'source'; src.className = 'hint';
    const accept = document.createElement('button');
    accept.type = 'button'; accept.textContent = 'Accept';
    const reject = document.createElement('button');
    reject.type = 'button'; reject.textContent = 'Reject';
    const verdict = document.createElement('span');
    verdict.className = 'hint';

    async function decide(decision) {
      accept.disabled = reject.disabled = true;
      verdict.textContent = ' working...';
      const r = unwrap(await callTask('review_candidate',
        { candidate_id: c.id, decision }), 'status');
      // Report what actually happened. An accept whose download failed is not
      // an accept.
      verdict.textContent = r && r.note ? ` ${r.note}` : ' done';
      if (r && r.status === 'accept_failed') row.style.opacity = '1';
      else row.style.opacity = '0.55';
    }
    accept.addEventListener('click', () => decide('accept'));
    reject.addEventListener('click', () => decide('reject'));

    row.append(img, meta, src, document.createElement('br'), accept, reject, verdict);
    candidateList.appendChild(row);
  }
}

function showTraining() {
  trainingTabBtn.classList.add('active');
  chatTabBtn.classList.remove('active');
  dashboardTabBtn.classList.remove('active');
  training.classList.remove('hidden');
  dashboard.classList.add('hidden');
  log.classList.add('hidden');
  form.classList.add('hidden');
  refreshTraining();
}

function showChat() {
  chatTabBtn.classList.add('active');
  dashboardTabBtn.classList.remove('active');
  trainingTabBtn.classList.remove('active');
  log.classList.remove('hidden');
  dashboard.classList.add('hidden');
  training.classList.add('hidden');
  form.classList.remove('hidden');
}

function showDashboard() {
  dashboardTabBtn.classList.add('active');
  chatTabBtn.classList.remove('active');
  trainingTabBtn.classList.remove('active');
  training.classList.add('hidden');
  dashboard.classList.remove('hidden');
  log.classList.add('hidden');
  form.classList.add('hidden');
  refreshDashboard();
}

chatTabBtn.addEventListener('click', showChat);
dashboardTabBtn.addEventListener('click', showDashboard);
trainingTabBtn.addEventListener('click', showTraining);
document.getElementById('refreshTraining').addEventListener('click', refreshTraining);
document.getElementById('sourceMoreBtn').addEventListener('click', async () => {
  const btn = document.getElementById('sourceMoreBtn');
  btn.disabled = true; btn.textContent = 'Searching...';
  await callTask('advance_campaign', { per_label: 3, max_labels: 2 });
  btn.disabled = false; btn.textContent = 'Find more';
  refreshTraining();
});
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
