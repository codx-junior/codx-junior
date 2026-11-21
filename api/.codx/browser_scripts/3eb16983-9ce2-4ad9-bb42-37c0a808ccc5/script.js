const ids = "{{ids}}".split(',').map(s=>s.trim()).filter(Boolean);
const out = [];
for (const id of ids) {
  try {
    const r = await fetch('/jobs/view/' + id + '/', { credentials: 'include' });
    const h = await r.text();
    const native = h.includes('aria-label="Easy Apply to this job"');
    const title = ((h.match(/<title>([^<]{0,70})/) || [])[1] || '').replace(/\s+/g, ' ');
    out.push({ id, native, status: r.status, title });
  } catch (e) { out.push({ id, native: null, error: e.message }); }
  await new Promise(r => setTimeout(r, 700));
}
return JSON.stringify({ total: out.length, native: out.filter(o => o.native).length, out });