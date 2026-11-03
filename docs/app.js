// NRSC Clips: paste a YouTube link, mark clips, make shorts. All GitHub calls go via the relay (no tokens here).
const RELAY = 'https://nrsc-shorts-relay.jessesebutinde.workers.dev';
const REPO = '/repos/Jessesebutinde/nrsc-shorts';
const $ = s => document.querySelector(s);
const LS = { get: (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } },
             set: (k, v) => localStorage.setItem(k, JSON.stringify(v)) };
let st = LS.get('nrsc.draft', { url: '', vid: '', clips: [], fmt: 'vertical', style: 'blur', title: '', join: true });
let myJobs = LS.get('nrsc.jobs', []);
let player = null, pendingStart = null;
const save = () => LS.set('nrsc.draft', st);
const fmtT = s => { s = Math.max(0, s); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = (s % 60).toFixed(1).padStart(4, '0');
  return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + x; };
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

async function api(path, opts = {}) {
  const res = await fetch(RELAY + REPO + path, { method: opts.method || 'GET', cache: 'no-store',
    headers: { 'Content-Type': 'application/json' }, body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (res.status === 404 && opts.allow404) return null;
  if (!res.ok) throw new Error('Server ' + res.status);
  return res.json();
}
const b64 = s => btoa(unescape(encodeURIComponent(s)));
const unb64 = s => decodeURIComponent(escape(atob(s.replace(/\n/g, ''))));

function parseId(u) {
  u = u.trim();
  if (/^[\w-]{11}$/.test(u)) return u;
  try {
    const x = new URL(u);
    if (x.hostname.includes('youtu.be')) return x.pathname.slice(1, 12);
    if (x.searchParams.get('v')) return x.searchParams.get('v');
    const m = x.pathname.match(/\/(live|shorts|embed|v)\/([\w-]{11})/); if (m) return m[2];
  } catch (e) { /* ignore */ }
  return null;
}

// ---------------- player
function loadVideo() {
  const id = parseId($('#url').value);
  if (!id) { $('#url-msg').textContent = 'That doesn\'t look like a YouTube link.'; $('#url-msg').className = 'msg err'; return; }
  if (id !== st.vid) { st.clips = []; pendingStart = null; }
  st.url = $('#url').value.trim(); st.vid = id; save();
  $('#url-msg').textContent = ''; $('#s-mark').hidden = false; $('#s-out').hidden = false;
  const make = () => {
    if (player) { player.cueVideoById(id); return; }
    player = new YT.Player('yt', { videoId: id, playerVars: { playsinline: 1, rel: 0, modestbranding: 1 } });
  };
  if (window.YT && YT.Player) make(); else window.onYouTubeIframeAPIReady = make;
  renderClips();
}
const now = () => (player && player.getCurrentTime ? player.getCurrentTime() : 0);
setInterval(() => { if (player && player.getCurrentTime) $('#now').textContent = fmtT(now()); }, 250);
document.querySelectorAll('[data-seek]').forEach(b => b.onclick = () => player && player.seekTo(Math.max(0, now() + +b.dataset.seek), true));
$('#pp').onclick = () => { if (!player) return; player.getPlayerState() === 1 ? player.pauseVideo() : player.playVideo(); };
$('#load').onclick = loadVideo;
$('#url').addEventListener('keydown', e => { if (e.key === 'Enter') loadVideo(); });

$('#m-start').onclick = () => { pendingStart = Math.round(now() * 10) / 10; $('#pending').textContent = 'Start marked at ' + fmtT(pendingStart) + ' — now play on and tap Mark end.'; };
$('#m-end').onclick = () => {
  const end = Math.round(now() * 10) / 10;
  if (pendingStart == null) { $('#pending').textContent = 'Tap Mark start first.'; return; }
  if (end <= pendingStart + 0.5) { $('#pending').textContent = 'End must be after the start.'; return; }
  st.clips.push({ name: 'Clip ' + (st.clips.length + 1), start: pendingStart, end });
  pendingStart = null; $('#pending').textContent = ''; save(); renderClips();
};
function renderClips() {
  const ol = $('#clips');
  if (!st.clips.length) { ol.innerHTML = '<p class="msg">No clips yet. Play the video, tap Mark start, then Mark end.</p>'; return; }
  ol.innerHTML = st.clips.map((c, i) => `<li><input data-i="${i}" class="cn" value="${esc(c.name)}">
    <div class="t">Start <b>${fmtT(c.start)}</b><button data-a="s-1" data-i="${i}">−1</button><button data-a="s+1" data-i="${i}">+1</button>
    End <b>${fmtT(c.end)}</b><button data-a="e-1" data-i="${i}">−1</button><button data-a="e+1" data-i="${i}">+1</button></div>
    <div class="t">(${fmtT(c.end - c.start)}) <button data-a="play" data-i="${i}">▶︎ Preview</button><button data-a="del" data-i="${i}">Delete</button></div></li>`).join('');
  ol.querySelectorAll('.cn').forEach(x => x.oninput = () => { st.clips[x.dataset.i].name = x.value; save(); });
  ol.querySelectorAll('button').forEach(b => b.onclick = () => {
    const c = st.clips[b.dataset.i], a = b.dataset.a;
    if (a === 'del') { if (confirm('Delete ' + c.name + '?')) st.clips.splice(b.dataset.i, 1); }
    else if (a === 'play') { player.seekTo(c.start, true); player.playVideo(); return; }
    else { const k = a[0] === 's' ? 'start' : 'end'; c[k] = Math.max(0, c[k] + (a.endsWith('-1') ? -1 : 1));
      if (c.end <= c.start + 0.5) c[k] = k === 'start' ? c.end - 1 : c.start + 1; player.seekTo(c[k], true); }
    save(); renderClips();
  });
}

// ---------------- output options
function seg(id, key) {
  document.querySelectorAll(`#${id} button`).forEach(b => {
    b.classList.toggle('on', b.dataset.v === st[key]);
    b.onclick = () => { st[key] = b.dataset.v; save(); seg(id, key); $('#style-wrap').hidden = st.fmt !== 'vertical'; };
  });
}
seg('fmt', 'fmt'); seg('style', 'style'); $('#style-wrap').hidden = st.fmt !== 'vertical';
$('#title').value = st.title; $('#title').oninput = () => { st.title = $('#title').value; save(); };
$('#join').checked = st.join; $('#join').onchange = () => { st.join = $('#join').checked; save(); };
$('#name').value = LS.get('nrsc.name', ''); $('#name').oninput = () => LS.set('nrsc.name', $('#name').value);

$('#make').onclick = async () => {
  const msg = $('#make-msg');
  if (!st.clips.length) { msg.className = 'msg err'; msg.textContent = 'Mark at least one clip first.'; return; }
  const id = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14) + '-' + Math.random().toString(36).slice(2, 6);
  const job = { id, url: 'https://www.youtube.com/watch?v=' + st.vid, video_id: st.vid, format: st.fmt, style: st.style,
    title: st.title.trim(), join: st.join && st.clips.length > 1, by: $('#name').value.trim() || 'Editor',
    created: new Date().toISOString(), clips: st.clips.map(c => ({ name: c.name.trim() || 'Clip', start: c.start, end: c.end })) };
  $('#make').disabled = true; msg.className = 'msg'; msg.textContent = 'Sending…';
  try {
    await api('/contents/jobs/' + id + '.json', { method: 'PUT', body: { message: 'New clip job ' + id + ' by ' + job.by, content: b64(JSON.stringify(job, null, 2)) } });
    myJobs.unshift({ id, created: job.created, n: job.clips.length }); LS.set('nrsc.jobs', myJobs.slice(0, 30));
    msg.className = 'msg ok'; msg.textContent = 'Sent! Your clips are being made — usually 2–6 minutes. Watch "Your videos" below.';
    refreshJobs();
  } catch (e) { msg.className = 'msg err'; msg.textContent = 'Could not send: ' + e.message + '. Check your internet and try again.'; }
  $('#make').disabled = false;
};

// ---------------- job status
let pollT = null;
async function refreshJobs() {
  clearTimeout(pollT);
  const box = $('#jobs');
  if (!myJobs.length) { box.innerHTML = '<p class="msg">Nothing made yet.</p>'; return; }
  let busy = false;
  const html = await Promise.all(myJobs.slice(0, 10).map(async j => {
    let s = null;
    try { const f = await api('/contents/status/jobs/' + j.id + '.json', { allow404: true }); if (f) s = JSON.parse(unb64(f.content)); } catch (e) { /* ignore */ }
    const when = new Date(j.created).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
    if (!s || !['done', 'failed'].includes(s.state)) busy = true;
    let body = '';
    if (!s) body = '<p class="msg">Waiting to start…</p>';
    else if (s.state === 'failed') body = `<p class="msg err">Failed: ${esc(s.message || 'unknown error')}</p>`;
    else if (s.state !== 'done') body = `<p class="msg">${esc(s.message || 'Working…')}</p>`;
    else body = (s.outputs || []).map(o => `<a href="${esc(o.url)}" target="_blank" rel="noopener">⬇ ${esc(o.name)} (${(o.size / 1048576).toFixed(1)} MB)</a>`).join('') || '<p class="msg">Done.</p>';
    return `<div class="job"><b>${when}</b> · ${j.n} clip${j.n > 1 ? 's' : ''}${body}</div>`;
  }));
  box.innerHTML = html.join('');
  if (busy) pollT = setTimeout(refreshJobs, 10000);
}

// ---------------- start
if (st.url) { $('#url').value = st.url; loadVideo(); }
refreshJobs();
