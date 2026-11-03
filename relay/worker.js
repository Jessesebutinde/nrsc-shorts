// NRSC Shorts relay: holds the GitHub token (secret GH_TOKEN) and forwards
// only this repo's metadata + Contents API calls. CORS: GitHub Pages origin only.
const ORIGIN = 'https://jessesebutinde.github.io';
const REPO = '/repos/Jessesebutinde/nrsc-shorts';
const cors = { 'Access-Control-Allow-Origin': ORIGIN, 'Access-Control-Allow-Methods': 'GET,PUT,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type,Accept,X-GitHub-Api-Version', 'Access-Control-Max-Age': '86400', Vary: 'Origin' };
export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
    const origin = req.headers.get('Origin');
    if (origin && origin !== ORIGIN) return new Response('forbidden', { status: 403 });
    const p = url.pathname;
    const ok = (p === REPO && req.method === 'GET') ||
      (p.startsWith(REPO + '/contents/') && !p.includes('..') && ['GET', 'PUT', 'DELETE'].includes(req.method));
    if (!ok) return new Response('not allowed', { status: 403, headers: cors });
    const r = await fetch('https://api.github.com' + p + url.search, { method: req.method,
      headers: { Authorization: 'Bearer ' + (env.GH_TOKEN || '').trim(), Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'nrsc-shorts-relay', 'Content-Type': 'application/json' },
      body: ['PUT', 'DELETE'].includes(req.method) ? await req.text() : undefined });
    const h = new Headers(cors); h.set('Content-Type', r.headers.get('Content-Type') || 'application/json');
    h.set('Cache-Control', 'no-store');
    return new Response(r.body, { status: r.status, headers: h });
  }
};
