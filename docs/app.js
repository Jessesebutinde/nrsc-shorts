/* Namasuba Shorts — phone app. Everything lives in the church GitHub repo:
   sources/<tag>.json (made by pack_and_ship), recipes/<id>.json (made here),
   status/<id>.json (made by the render workflow), videos in Releases. */
'use strict';

const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const LS = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* ignore */ } },
  del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
};
let cfg = LS.get('nrsc.cfg', { owner: 'Jessesebutinde', repo: 'nrsc-shorts', token: '', name: '' });

const JOINS = {
  cut:  { label: 'Cut',          help: 'Straight to the next clip. Use this most of the time.' },
  fade: { label: 'Fade',         help: 'Pictures blend for half a second.', dur: 0.5 },
  jcut: { label: 'J-cut',        help: 'You hear the next clip 1.5 s before you see it.', lead: 1.5 },
  lcut: { label: 'L-cut',        help: 'This clip keeps talking while the next picture starts.', lag: 1.5 },
  dip:  { label: 'Dip to black', help: 'A quick black between two separate ideas.', dur: 0.6 },
};
// kbps for size estimates (video + audio), same numbers as worker/render.py
const EST = { whatsapp_long: 2500 + 128, whatsapp_saver: 1100 + 96, tiktok: 6000 + 160 };

// ------------------------------------------------------------------ GitHub
const API = 'https://api.github.com';
async function gh(path, opts = {}) {
  const headers = { Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' };
  if (cfg.token) headers.Authorization = 'Bearer ' + cfg.token;
  if (opts.body) headers['Content-Type'] = 'application/json';
  const res = await fetch(API + path, { method: opts.method || 'GET', headers, cache: 'no-store',
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (res.status === 404 && opts.allow404) return null;
  if (!res.ok) {
    let m = ''; try { m = (await res.json()).message || ''; } catch (e) { /* ignore */ }
    throw new Error(`GitHub ${res.status}: ${m}`);
  }
  if (res.status === 204) return null;
  return res.json();
}
const repoPath = p => `/repos/${cfg.owner}/${cfg.repo}/contents/${p}`;
function b64decode(b64) {
  const bin = atob(b64.replace(/\n/g, ''));
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}
function b64encode(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}
async function listDir(dir) {
  const r = await gh(repoPath(dir), { allow404: true });
  return (Array.isArray(r) ? r : []).filter(f => f.type === 'file' && f.name.endsWith('.json'));
}
async function getJson(path) {
  const r = await gh(repoPath(path), { allow404: true });
  if (!r) return null;
  return { data: JSON.parse(b64decode(r.content)), sha: r.sha };
}
async function putJson(path, obj, message) {
  const cur = await gh(repoPath(path), { allow404: true });
  const body = { message, content: b64encode(JSON.stringify(obj, null, 2)) };
  if (cur && cur.sha) body.sha = cur.sha;
  return gh(repoPath(path), { method: 'PUT', body });
}
async function deleteFile(path, message) {
  const cur = await gh(repoPath(path), { allow404: true });
  if (cur) await gh(repoPath(path), { method: 'DELETE', body: { message, sha: cur.sha } });
}
const assetUrl = (tag, name) => `https://github.com/${cfg.owner}/${cfg.repo}/releases/download/${tag}/${name}`;

// ------------------------------------------------------------------ helpers
function fmt(t, dec = 1) {
  if (t == null || isNaN(t)) return '—';
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  const ss = dec ? s.toFixed(dec).padStart(3 + dec, '0') : String(Math.floor(s)).padStart(2, '0');
  return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + ss;
}
const r1 = x => Math.round(x * 10) / 10;
function toast(msg, ms = 2200) {
  const t = $('#toast'); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._h); toast._h = setTimeout(() => { t.hidden = true; }, ms);
}
function show(id) {
  $$('.screen').forEach(s => { s.hidden = s.id !== id; });
  window.scrollTo(0, 0);
  if (id !== 's-mark') { video.pause(); }
  if (id !== 's-confirm') stopPolling();
}
function tagInfo(tag) {
  const m = /^src-(\d{4}-\d{2}-\d{2})-(.+)$/.exec(tag);
  if (!m) return { date: '', service: tag, title: tag };
  const d = new Date(m[1] + 'T12:00:00');
  const service = m[2].replace(/[-_]+/g, ' ').replace(/^\w/, c => c.toUpperCase());
  const title = d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }) + ' · ' + service;
  return { date: m[1], service, title };
}
const complete = c => c && c.in != null && c.out != null && c.out > c.in + 0.4;
function clipLen(c) { return complete(c) ? c.out - c.in : 0; }
function totalLen(r) {
  let t = 0;
  const cs = r.clips.filter(complete);
  cs.forEach((c, i) => {
    t += clipLen(c);
    const j = c.join_after && c.join_after.type;
    if (i < cs.length - 1 && (j === 'fade' || j === 'dip')) t -= c.join_after.dur || JOINS[j].dur;
  });
  return t;
}

// ------------------------------------------------------------------ state
let sources = [];      // [{tag, title, date, clips}]
let src = null;        // full sources/<tag>.json
let recipe = null;
let sel = -1;
let lastMark = 'in';
let env = null;        // Uint8Array loudness levels, 2 per second
let duration = 0;
let playUntil = null;
let joinQueue = null;
const video = $('#video');

// ------------------------------------------------------------------ settings
function openSettings(msg = '') {
  $('#cfg-owner').value = cfg.owner || ''; $('#cfg-repo').value = cfg.repo || '';
  $('#cfg-token').value = cfg.token || ''; $('#cfg-name').value = cfg.name || '';
  $('#cfg-msg').textContent = msg; $('#cfg-msg').className = 'msg';
  show('s-settings');
}
function readCfg() {
  return { owner: $('#cfg-owner').value.trim(), repo: $('#cfg-repo').value.trim(),
    token: $('#cfg-token').value.trim(), name: $('#cfg-name').value.trim() };
}
$('#cfg-test').onclick = async () => {
  cfg = readCfg();
  const m = $('#cfg-msg');
  try {
    const r = await gh(`/repos/${cfg.owner}/${cfg.repo}`);
    m.textContent = `Connected to ${r.full_name}` + (r.permissions && r.permissions.push ? ' (can save clips)' : ' (read only — check the token)');
    m.className = 'msg ok';
  } catch (e) { m.textContent = e.message; m.className = 'msg bad'; }
};
$('#cfg-save').onclick = () => { cfg = readCfg(); LS.set('nrsc.cfg', cfg); loadLibrary(); };
$('#lib-settings').onclick = () => openSettings();

// ------------------------------------------------------------------ library
async function loadLibrary() {
  show('s-library');
  const msg = $('#lib-msg'); msg.textContent = 'Loading…'; msg.className = 'msg';
  try {
    const [files, recipes] = await Promise.all([listDir('sources'), listDir('recipes')]);
    sources = files.map(f => {
      const tag = f.name.replace(/\.json$/, '');
      const info = tagInfo(tag);
      const clips = recipes.filter(r => r.name.startsWith(tag.replace(/^src-/, '') + '__')).length;
      return { tag, ...info, clips };
    }).sort((a, b) => (b.date + b.tag).localeCompare(a.date + a.tag));
    msg.textContent = sources.length ? '' : 'No services yet. Run pack and ship on the church PC after a stream.';
    const list = $('#lib-list'); list.innerHTML = '';
    sources.forEach(s => {
      const el = document.createElement('div');
      el.className = 'card tap';
      el.innerHTML = `<div class="t"><b></b><small></small></div><span class="badge">${s.clips} clip${s.clips === 1 ? '' : 's'}</span><span class="icon">›</span>`;
      el.querySelector('b').textContent = s.title;
      el.querySelector('small').textContent = s.tag;
      el.onclick = () => openSource(s.tag);
      list.appendChild(el);
    });
  } catch (e) {
    msg.textContent = e.message; msg.className = 'msg bad';
    if (/401|403/.test(e.message)) openSettings('The token was refused. Check it and save again.');
  }
}
$('#lib-refresh').onclick = loadLibrary;

// ------------------------------------------------------------------ clips of one source
async function openSource(tag) {
  show('s-clips');
  $('#clips-title').textContent = tagInfo(tag).title;
  $('#clips-sub').textContent = 'Loading…';
  $('#clips-list').innerHTML = '';
  try {
    const got = await getJson(`sources/${tag}.json`);
    if (!got) throw new Error('Source file missing');
    src = got.data;
    env = decodeEnv(src.env || '');
    duration = src.duration || 0;
    $('#clips-sub').textContent = `${fmt(duration, 0)} long · ` + (src.source_ready ? 'full-quality source is uploaded' : 'full-quality source still uploading — you can mark now, rendering waits');
    await listClips(tag);
  } catch (e) { $('#clips-sub').textContent = e.message; }
}
async function listClips(tag) {
  const list = $('#clips-list'); list.innerHTML = '';
  const prefix = tag.replace(/^src-/, '') + '__';
  const files = (await listDir('recipes')).filter(f => f.name.startsWith(prefix));
  const items = await Promise.all(files.map(async f => {
    const id = f.name.replace(/\.json$/, '');
    const [r, s] = await Promise.all([getJson(`recipes/${id}.json`), getJson(`status/${id}.json`)]);
    return { id, recipe: r && r.data, status: s && s.data };
  }));
  items.sort((a, b) => (b.recipe && b.recipe.updated || '').localeCompare(a.recipe && a.recipe.updated || ''));
  items.forEach(it => {
    if (!it.recipe) return;
    const r = it.recipe, s = it.status;
    const st = stageBadge(r, s);
    const el = document.createElement('div');
    el.className = 'card tap';
    el.innerHTML = `<div class="t"><b></b><small></small></div><span class="badge ${st.cls}">${st.text}</span><div class="ctl"><button title="Delete">✕</button></div>`;
    el.querySelector('b').textContent = r.title || 'Untitled clip';
    el.querySelector('small').textContent = `${r.clips.filter(complete).length} pieces · ${fmt(totalLen(r), 0)} · by ${r.by || '?'}`;
    el.querySelector('.t').onclick = () => openEditor(r);
    el.querySelector('span.badge').onclick = () => openEditor(r, s && (s.stage === 'draft_ready' || s.stage === 'final_ready' || s.stage === 'rendering') ? 's-confirm' : 's-mark');
    const del = el.querySelector('button');
    del.onclick = async () => {
      if (!del.dataset.armed) { del.dataset.armed = '1'; del.textContent = '!'; toast('Tap ✕ again to delete this clip'); setTimeout(() => { del.dataset.armed = ''; del.textContent = '✕'; }, 3000); return; }
      try { await deleteFile(`recipes/${r.id}.json`, `delete ${r.id}`); await deleteFile(`status/${r.id}.json`, `delete status ${r.id}`); LS.del('nrsc.recipe.' + r.id); el.remove(); toast('Deleted'); }
      catch (e) { toast(e.message); }
    };
    list.appendChild(el);
  });
  if (!items.length) list.innerHTML = '<p class="hint">No clips yet for this service.</p>';
}
function stageBadge(r, s) {
  if (!s || s.rev !== r.rev) return r.want === 'none' || !r.want ? { text: 'saved', cls: '' } : { text: 'queued', cls: 'warn' };
  return { rendering: { text: 'rendering', cls: 'warn' }, draft_ready: { text: 'draft ready', cls: 'ok' },
    final_ready: { text: 'final ready', cls: 'ok' }, waiting_source: { text: 'waiting source', cls: 'warn' },
    error: { text: 'error', cls: 'bad' } }[s.stage] || { text: s.stage, cls: '' };
}
$('#clips-back').onclick = loadLibrary;
$('#clips-new').onclick = () => openEditor({
  id: `${src.tag.replace(/^src-/, '')}__${Date.now().toString(36)}`, source: src.tag, title: '', rev: 0, want: 'none',
  outputs: ['whatsapp_long', 'whatsapp_saver', 'tiktok'], clips: [],
});

// ------------------------------------------------------------------ editor
function decodeEnv(s) {
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = parseInt(s[i], 36) || 0;
  return out;
}
function openEditor(r, screen = 's-mark') {
  const local = LS.get('nrsc.recipe.' + r.id);
  recipe = (local && local.dirty && (local.rev || 0) >= (r.rev || 0)) ? local : JSON.parse(JSON.stringify(r));
  if (recipe.dirty) toast('Restored unsaved marks from this phone');
  sel = recipe.clips.length ? recipe.clips.length - 1 : -1;
  lastMark = 'in';
  $('#mark-title').textContent = recipe.title || tagInfo(src.tag).title;
  const url = assetUrl(src.tag, 'mark.mp4');
  if (video.getAttribute('src') !== url) { video.setAttribute('src', url); video.load(); }
  show(screen);
  if (screen === 's-confirm') openConfirm(); else { renderStrip(); updateFrame(); sizeCanvases(); draw(); }
  if (sel >= 0 && recipe.clips[sel].in != null) seek(recipe.clips[sel].in);
}
function dirty() { recipe.dirty = true; LS.set('nrsc.recipe.' + recipe.id, recipe); }
const now = () => video.currentTime || 0;
function seek(t) { video.currentTime = Math.max(0, Math.min(duration || t, t)); draw(); }
function curClip() { return sel >= 0 ? recipe.clips[sel] : null; }

function ensureClip(forMark) {
  const c = curClip();
  const t = now();
  if (!c) return newClip();
  // tapping IN after a finished clip's OUT means "start the next clip"
  if (forMark === 'in' && complete(c) && t > c.out) return newClip();
  if (forMark === 'out' && complete(c) && t < c.in) return newClip();
  return c;
}
function newClip() {
  const c = { in: null, out: null, frame_x: 0.5, look: 'tight', join_after: { type: 'cut' } };
  recipe.clips.push(c); sel = recipe.clips.length - 1; return c;
}
$('#mark-in').onclick = () => { const c = ensureClip('in'); c.in = r1(now()); if (c.out != null && c.out <= c.in) c.out = null; lastMark = 'in'; dirty(); renderStrip(); draw(); };
$('#mark-out').onclick = () => { const c = ensureClip('out'); c.out = r1(now()); if (c.in != null && c.in >= c.out) c.in = null; lastMark = 'out'; dirty(); renderStrip(); draw(); };
$$('[data-nudge]').forEach(b => b.onclick = () => {
  const c = curClip(); if (!c || c[lastMark] == null) return toast('Set IN or OUT first');
  c[lastMark] = r1(Math.max(0, c[lastMark] + parseFloat(b.dataset.nudge)));
  if (complete(c) === false && c.in != null && c.out != null) toast('OUT must come after IN');
  dirty(); seek(c[lastMark]); renderStrip();
});
$('#snap').onclick = () => {
  const c = curClip(); if (!c || c[lastMark] == null) return toast('Set IN or OUT first');
  const pauses = src.pauses || [];
  if (!pauses.length) return toast('No pause list for this service');
  const t = c[lastMark];
  let best = null, bd = 1e9;
  for (const p of pauses) { const d = t < p[0] ? p[0] - t : t > p[1] ? t - p[1] : 0; if (d < bd) { bd = d; best = p; } }
  if (!best || bd > 4) return toast('No pause within 4 s');
  c[lastMark] = r1(lastMark === 'in' ? Math.max(best[0], best[1] - 0.15) : Math.min(best[1], best[0] + 0.3));
  dirty(); seek(c[lastMark]); renderStrip(); toast(`${lastMark.toUpperCase()} moved to the pause`);
};
$$('[data-skip]').forEach(b => b.onclick = () => seek(now() + parseFloat(b.dataset.skip)));
$('#play').onclick = () => { playUntil = null; if (video.paused) video.play(); else video.pause(); };
$('#play-clip').onclick = () => playRange(curClip());
function playRange(c, from, to) {
  if (!c || !complete(c)) return toast('Finish the clip first (IN and OUT)');
  video.currentTime = from != null ? from : c.in; playUntil = to != null ? to : c.out; video.play();
}
video.addEventListener('play', () => { $('#play').textContent = '❚❚'; });
video.addEventListener('pause', () => { $('#play').textContent = '▶'; });
video.addEventListener('loadedmetadata', () => { if (video.duration && isFinite(video.duration)) duration = video.duration; sizeCanvases(); draw(); });
video.addEventListener('timeupdate', () => {
  if (playUntil != null && video.currentTime >= playUntil) { video.pause(); playUntil = null; if (joinQueue) { const q = joinQueue; joinQueue = null; q(); } }
});
$('#mark-back').onclick = () => { video.pause(); openSource(src.tag); };
$('#mark-next').onclick = () => { video.pause(); openArrange(); };

// frame (9:16 window)
function activeFrameX(c, t) {
  if (!c) return 0.5;
  if (c.frame && c.frame.length) {
    const rel = t - (c.in || 0); let x = c.frame[0].x;
    for (const p of c.frame) if (p.t <= rel) x = p.x;
    return x;
  }
  return c.frame_x != null ? c.frame_x : 0.5;
}
function updateFrame() {
  const c = curClip();
  const stage = $('.stage');
  stage.classList.toggle('stagelook', !!(c && c.look === 'stage'));
  $('#look').textContent = c && c.look === 'stage' ? 'Stage' : 'Tight';
  const x = activeFrameX(c, now());
  const w = stage.clientWidth, h = stage.clientHeight;
  const vw = Math.min(w, h * 16 / 9), vh = vw * 9 / 16;        // where the picture really sits (object-fit: contain)
  const fw = vh * 9 / 16, left = (w - vw) / 2 + (vw - fw) * x;
  const f = $('#frame');
  f.style.left = left + 'px'; f.style.width = fw + 'px'; f.style.top = (h - vh) / 2 + 'px'; f.style.height = vh + 'px';
  if (document.activeElement !== $('#frame-x')) $('#frame-x').value = Math.round(x * 100);
}
$('#frame-x').oninput = () => {
  const c = curClip(); if (!c) return;
  const x = $('#frame-x').value / 100;
  if (c.frame && c.frame.length) {
    const rel = now() - (c.in || 0); let p = c.frame[0];
    for (const q of c.frame) if (q.t <= rel) p = q;
    p.x = x;
  } else c.frame_x = x;
  dirty(); updateFrame();
};
$('#frame-here').onclick = () => {
  const c = curClip(); if (!c || c.in == null) return toast('Set IN first');
  const rel = r1(Math.max(0, now() - c.in));
  if (!c.frame) c.frame = [{ t: 0, x: c.frame_x != null ? c.frame_x : 0.5 }];
  const x = $('#frame-x').value / 100;
  const ex = c.frame.find(p => Math.abs(p.t - rel) < 0.3);
  if (ex) ex.x = x; else c.frame.push({ t: rel, x });
  c.frame.sort((a, b) => a.t - b.t);
  dirty(); toast(`Frame point at +${rel}s`);
};
$('#look').onclick = () => { const c = curClip(); if (!c) return; c.look = c.look === 'stage' ? 'tight' : 'stage'; dirty(); updateFrame(); };

// clip strip
function renderStrip() {
  const s = $('#strip'); s.innerHTML = '';
  recipe.clips.forEach((c, i) => {
    const el = document.createElement('div');
    el.className = 'clip' + (i === sel ? ' sel' : '');
    const len = complete(c) ? fmt(clipLen(c)) + 's' : (c.in == null ? 'set IN' : 'set OUT');
    el.innerHTML = `${i + 1} · ${len}<small>${fmt(c.in, 0)} – ${fmt(c.out, 0)}</small>`;
    el.onclick = () => { sel = i; renderStrip(); updateFrame(); if (c.in != null) seek(c.in); };
    s.appendChild(el);
  });
  const n = document.createElement('div');
  n.className = 'clip new'; n.textContent = '+ New';
  n.onclick = () => { newClip(); dirty(); renderStrip(); updateFrame(); toast('New clip: tap IN'); };
  s.appendChild(n);
}

// canvases
const zoom = $('#zoom'), over = $('#overview');
const ZOOM_SECS = 40;
function sizeCanvases() {
  const dpr = window.devicePixelRatio || 1;
  for (const c of [zoom, over]) { c.width = c.clientWidth * dpr; c.height = c.clientHeight * dpr; }
}
function draw() {
  if ($('#s-mark').hidden) return;
  const t = now(); $('#tc-now').textContent = fmt(t);
  const c = curClip();
  $('#tc-in').textContent = 'in ' + (c ? fmt(c.in) : '—'); $('#tc-out').textContent = 'out ' + (c ? fmt(c.out) : '—');
  updateFrame();
  const cs = getComputedStyle(document.documentElement);
  const gold = cs.getPropertyValue('--gold').trim(), purple = cs.getPropertyValue('--purple').trim(), line = cs.getPropertyValue('--line').trim();
  // zoom strip
  let ctx = zoom.getContext('2d'); let W = zoom.width, H = zoom.height;
  ctx.clearRect(0, 0, W, H);
  const pps = W / ZOOM_SECS, t0 = t - ZOOM_SECS / 2;
  for (const p of (src.pauses || [])) { if (p[1] < t0 || p[0] > t0 + ZOOM_SECS) continue; ctx.fillStyle = 'rgba(255,255,255,.07)'; ctx.fillRect((p[0] - t0) * pps, 0, Math.max(1, (p[1] - p[0]) * pps), H); }
  if (env && env.length) {
    ctx.fillStyle = '#6E55B8';
    const i0 = Math.max(0, Math.floor(t0 * 2)), i1 = Math.min(env.length, Math.ceil((t0 + ZOOM_SECS) * 2));
    for (let i = i0; i < i1; i++) { const x = (i / 2 - t0) * pps, h = (env[i] / 35) * (H - 6); ctx.fillRect(x, H - h, Math.max(1, pps / 2 - 1), h); }
  }
  recipe.clips.forEach((k, i) => {
    if (!complete(k)) return;
    ctx.fillStyle = i === sel ? 'rgba(240,180,70,.22)' : 'rgba(176,148,255,.18)';
    ctx.fillRect((k.in - t0) * pps, 0, (k.out - k.in) * pps, H);
  });
  if (c) {
    ctx.fillStyle = gold;
    if (c.in != null) ctx.fillRect((c.in - t0) * pps - 1, 0, 3, H);
    if (c.out != null) ctx.fillRect((c.out - t0) * pps - 1, 0, 3, H);
  }
  ctx.fillStyle = '#fff'; ctx.fillRect(W / 2 - 1, 0, 2, H);
  for (const p of (src.scenes || [])) { if (p < t0 || p > t0 + ZOOM_SECS) continue; ctx.fillStyle = 'rgba(255,255,255,.35)'; ctx.fillRect((p - t0) * pps, 0, 1, 8); }
  // overview
  ctx = over.getContext('2d'); W = over.width; H = over.height;
  ctx.clearRect(0, 0, W, H);
  if (env && env.length && duration) {
    ctx.fillStyle = line;
    const step = Math.max(1, Math.floor(env.length / W));
    for (let x = 0; x < W; x++) { let m = 0; const i0 = Math.floor(x / W * env.length); for (let i = i0; i < i0 + step && i < env.length; i++) m = Math.max(m, env[i]); const h = m / 35 * H; ctx.fillRect(x, H - h, 1, h); }
  }
  recipe.clips.forEach((k, i) => { if (!complete(k) || !duration) return; ctx.fillStyle = i === sel ? gold : purple; ctx.fillRect(k.in / duration * W, 0, Math.max(2, (k.out - k.in) / duration * W), H); });
  if (duration) { ctx.fillStyle = '#fff'; ctx.fillRect(t / duration * W - 1, 0, 2, H); }
}
(function loop() { if (!$('#s-mark').hidden && !video.paused) draw(); requestAnimationFrame(loop); })();
video.addEventListener('seeked', draw);
window.addEventListener('resize', () => { sizeCanvases(); draw(); });
// drag the zoom strip to scrub
let drag = null;
zoom.addEventListener('pointerdown', e => { zoom.setPointerCapture(e.pointerId); drag = { x: e.clientX, t: now() }; video.pause(); playUntil = null; });
zoom.addEventListener('pointermove', e => { if (!drag) return; const pps = zoom.clientWidth / ZOOM_SECS; seek(drag.t - (e.clientX - drag.x) / pps); });
zoom.addEventListener('pointerup', () => { drag = null; });
zoom.addEventListener('pointercancel', () => { drag = null; });
over.addEventListener('pointerdown', e => { if (!duration) return; const r = over.getBoundingClientRect(); seek((e.clientX - r.left) / r.width * duration); });

// ------------------------------------------------------------------ arrange
function openArrange() {
  show('s-arrange');
  const list = $('#arr-list'); list.innerHTML = '';
  const cs = recipe.clips;
  cs.forEach((c, i) => {
    const el = document.createElement('div');
    el.className = 'card';
    el.innerHTML = `<div class="t tap"><b></b><small></small></div><div class="ctl"><button title="Up">▲</button><button title="Down">▼</button><button title="Remove">✕</button></div>`;
    el.querySelector('b').textContent = `${i + 1} · ${complete(c) ? fmt(clipLen(c)) + ' s' : 'incomplete'}`;
    el.querySelector('small').textContent = `${fmt(c.in)} → ${fmt(c.out)} · ${c.look === 'stage' ? 'stage' : 'tight'} · then ${JOINS[(c.join_after || {}).type || 'cut'].label}`;
    el.querySelector('.t').onclick = () => { sel = i; show('s-mark'); renderStrip(); sizeCanvases(); draw(); playRange(c); };
    const [up, down, del] = el.querySelectorAll('button');
    up.onclick = () => { if (i > 0) { [cs[i - 1], cs[i]] = [cs[i], cs[i - 1]]; dirty(); openArrange(); } };
    down.onclick = () => { if (i < cs.length - 1) { [cs[i + 1], cs[i]] = [cs[i], cs[i + 1]]; dirty(); openArrange(); } };
    del.onclick = () => { cs.splice(i, 1); sel = Math.min(sel, cs.length - 1); dirty(); openArrange(); };
    list.appendChild(el);
  });
  if (!cs.length) list.innerHTML = '<p class="hint">No clips yet. Go back and tap IN / OUT.</p>';
}
$('#arr-back').onclick = () => { show('s-mark'); renderStrip(); sizeCanvases(); draw(); };
$('#arr-next').onclick = openJoins;

// ------------------------------------------------------------------ joins
function openJoins() {
  show('s-joins');
  const list = $('#join-list'); list.innerHTML = '';
  const cs = recipe.clips.filter(complete);
  cs.forEach((c, i) => {
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `<div class="t"><b></b><small></small></div>`;
    card.querySelector('b').textContent = `${i + 1} · ${fmt(clipLen(c))} s`;
    card.querySelector('small').textContent = `${fmt(c.in)} → ${fmt(c.out)}`;
    list.appendChild(card);
    if (i === cs.length - 1) return;
    c.join_after = c.join_after || { type: 'cut' };
    const row = document.createElement('div'); row.className = 'joinrow';
    const btn = document.createElement('button'); btn.className = 'joinbtn';
    btn.textContent = '⌄ ' + JOINS[c.join_after.type || 'cut'].label;
    row.appendChild(btn); list.appendChild(row);
    const pick = document.createElement('div'); pick.className = 'joinpick'; pick.hidden = true;
    for (const [k, j] of Object.entries(JOINS)) {
      const b = document.createElement('button');
      b.className = k === c.join_after.type ? 'on' : '';
      b.innerHTML = `<b>${j.label}</b><small>${j.help}</small>`;
      b.onclick = () => {
        c.join_after = { type: k }; if (j.dur) c.join_after.dur = j.dur; if (j.lead) c.join_after.lead = j.lead; if (j.lag) c.join_after.lag = j.lag;
        dirty(); openJoins();
      };
      pick.appendChild(b);
    }
    const play = document.createElement('button'); play.className = 'play'; play.textContent = '▶ Play this join (3 s before, 3 s after)';
    play.onclick = () => {
      const nxt = cs[i + 1];
      show('s-mark'); renderStrip(); sizeCanvases(); draw();
      joinQueue = () => { playRange(nxt, nxt.in, Math.min(nxt.out, nxt.in + 3)); joinQueue = () => { openJoins(); }; };
      playRange(c, Math.max(c.in, c.out - 3), c.out);
    };
    pick.appendChild(play);
    list.appendChild(pick);
    btn.onclick = () => { pick.hidden = !pick.hidden; };
  });
  if (cs.length < 2) list.innerHTML += '<p class="hint">Joins appear once there are two or more finished clips.</p>';
}
$('#join-back').onclick = openArrange;
$('#join-next').onclick = openConfirm;

// ------------------------------------------------------------------ confirm
let pollTimer = null;
function stopPolling() { clearTimeout(pollTimer); pollTimer = null; }
function openConfirm() {
  show('s-confirm');
  $('#conf-title').value = recipe.title || '';
  $$('[data-out]').forEach(cb => { cb.checked = (recipe.outputs || []).includes(cb.dataset.out); });
  const cs = recipe.clips.filter(complete);
  const len = totalLen(recipe);
  $('#conf-summary').textContent = `${cs.length} piece${cs.length === 1 ? '' : 's'} · ${fmt(len)} s long · ` + cs.map((c, i) => (i ? JOINS[(cs[i - 1].join_after || {}).type || 'cut'].label + ' → ' : '') + fmt(clipLen(c), 0)).join(' ');
  $$('[data-est]').forEach(s => { s.textContent = '≈ ' + (EST[s.dataset.est] * len / 8000).toFixed(0) + ' MB'; });
  $('#conf-draft').disabled = $('#conf-save').disabled = !cs.length;
  $('#conf-draftbox').hidden = true; $('#conf-files').innerHTML = '';
  refreshStatus();
}
function readConfirm() {
  recipe.title = $('#conf-title').value.trim();
  recipe.outputs = $$('[data-out]').filter(cb => cb.checked).map(cb => cb.dataset.out);
}
async function saveRecipe(want) {
  readConfirm();
  const r = JSON.parse(JSON.stringify(recipe));
  r.clips = r.clips.filter(complete);
  if (r.clips.length) delete r.clips[r.clips.length - 1].join_after;
  r.want = want; r.rev = (recipe.rev || 0) + 1; r.by = cfg.name || 'unknown'; r.updated = new Date().toISOString();
  delete r.dirty;
  const msgs = { none: 'save', draft: 'draft', final: 'final' };
  await putJson(`recipes/${r.id}.json`, r, `${msgs[want]}: ${r.title || r.id}`);
  recipe.rev = r.rev; recipe.want = want; recipe.dirty = false; LS.del('nrsc.recipe.' + recipe.id);
}
async function act(want, btn) {
  btn.disabled = true;
  try { await saveRecipe(want); toast(want === 'none' ? 'Saved' : 'Sent to GitHub'); refreshStatus(); }
  catch (e) { toast(e.message, 4000); }
  btn.disabled = false;
}
$('#conf-save').onclick = e => act('none', e.target);
$('#conf-draft').onclick = e => act('draft', e.target);
$('#conf-final').onclick = e => act('final', e.target);
$('#conf-change').onclick = () => { show('s-mark'); renderStrip(); sizeCanvases(); draw(); };
$('#conf-back').onclick = openJoins;
$('#conf-home').onclick = loadLibrary;
async function refreshStatus() {
  stopPolling();
  const box = $('#conf-status');
  let s = null;
  try { const got = await getJson(`status/${recipe.id}.json`); s = got && got.data; } catch (e) { box.textContent = e.message; }
  const want = recipe.want || 'none';
  let stage = '', text = '', cls = '', keep = false;
  if (want === 'none') { stage = 'Not sent yet'; text = 'Press Make draft when the marks look right.'; }
  else if (!s || s.rev !== recipe.rev) { stage = 'Queued'; text = 'GitHub is waking up. A draft usually takes 1–3 minutes.'; cls = 'rendering'; keep = true; }
  else if (s.stage === 'rendering') { stage = 'Rendering…'; text = s.message || ''; cls = 'rendering'; keep = true; }
  else if (s.stage === 'draft_ready') { stage = 'Draft ready'; text = s.message || ''; cls = 'ready'; }
  else if (s.stage === 'final_ready') { stage = 'Final ready'; text = s.message || ''; cls = 'ready'; }
  else if (s.stage === 'waiting_source') { stage = 'Waiting for the source video'; text = s.message || ''; cls = 'rendering'; keep = true; }
  else if (s.stage === 'error') { stage = 'Something went wrong'; text = s.message || ''; cls = 'error'; }
  box.className = 'status ' + cls;
  box.innerHTML = `<div class="st-stage"></div><div class="txt"></div><a class="hint" target="_blank" rel="noopener" href="https://github.com/${cfg.owner}/${cfg.repo}/actions">See the render log on GitHub</a>`;
  box.querySelector('.st-stage').textContent = stage; box.querySelector('.txt').textContent = text;
  const files = (s && s.rev === recipe.rev && s.files) || {};
  const dbox = $('#conf-draftbox');
  if (files.draft && (s.stage === 'draft_ready' || s.stage === 'final_ready')) {
    dbox.hidden = false; const dv = $('#draft-video'); if (dv.getAttribute('src') !== files.draft) dv.src = files.draft;
    $('#conf-final').hidden = s.stage === 'final_ready';
  } else dbox.hidden = true;
  const fl = $('#conf-files'); fl.innerHTML = '';
  if (s && s.stage === 'final_ready' && s.rev === recipe.rev) {
    const names = { whatsapp_long: 'WhatsApp HD (send as document)', whatsapp_saver: 'WhatsApp data-saver (send as document)', tiktok: 'TikTok / Shorts 1080p', status: 'WhatsApp Status' };
    for (const [k, url] of Object.entries(files)) {
      if (k === 'draft') continue;
      const a = document.createElement('a'); a.href = url; a.target = '_blank'; a.rel = 'noopener';
      a.innerHTML = `<span class="n"></span><span>${s.sizes_mb && s.sizes_mb[k] ? s.sizes_mb[k] + ' MB' : ''}</span>`;
      a.querySelector('.n').textContent = names[k] || k;
      fl.appendChild(a);
    }
  }
  if (keep) pollTimer = setTimeout(refreshStatus, 8000);
}

// ------------------------------------------------------------------ start
(function start() {
  if (!cfg.token || !cfg.owner || !cfg.repo) openSettings();
  else loadLibrary();
})();
