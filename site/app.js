'use strict';

const CATEGORIES = ['โมเดลใหม่', 'ผลิตภัณฑ์/ฟีเจอร์', 'งานวิจัย', 'ธุรกิจ/การลงทุน', 'นโยบาย/กฎหมาย', 'เครื่องมือ/โอเพนซอร์ส', 'อื่นๆ'];
const PAGE = 30;            // แสดงทีละกี่ข่าว แล้วกด "แสดงเพิ่ม"
const STALE_HOURS = 12;     // ข้อมูลเก่ากว่านี้ = งานอัปเดตอัตโนมัติน่าจะมีปัญหา
const TZ = 'Asia/Bangkok';

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const safeUrl = (u) => (/^https?:\/\//i.test(u || '') ? u : '#');
const num = (n) => n.toLocaleString('th-TH');

const ICON = {
  ext: '<svg viewBox="0 0 24 24" class="icon" aria-hidden="true"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>',
  alert: '<svg viewBox="0 0 24 24" class="icon" aria-hidden="true"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>',
  error: '<svg viewBox="0 0 24 24" class="icon" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/></svg>',
  inbox: '<svg viewBox="0 0 24 24" class="icon" aria-hidden="true"><path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>',
  search: '<svg viewBox="0 0 24 24" class="icon" aria-hidden="true"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>',
};

// ---------- วันเวลา (DD/MM/YYYY ค.ศ. เวลาไทย) ----------
const fmtFull = new Intl.DateTimeFormat('th-TH-u-ca-gregory', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: TZ });
const fmtTime = new Intl.DateTimeFormat('th-TH-u-ca-gregory', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: TZ });
const fmtDay = new Intl.DateTimeFormat('th-TH-u-ca-gregory', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', timeZone: TZ });
const fmtKey = new Intl.DateTimeFormat('en-CA', { timeZone: TZ });

const dayKey = (d) => fmtKey.format(d);
function ago(iso) {
  const min = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (min < 1) return 'เมื่อสักครู่';
  if (min < 60) return `${min} นาทีที่แล้ว`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h} ชม. ที่แล้ว`;
  return `${Math.floor(h / 24)} วันที่แล้ว`;
}
function dayLabel(key) {
  const now = new Date();
  if (key === dayKey(now)) return 'วันนี้';
  if (key === dayKey(new Date(now.getTime() - 86400000))) return 'เมื่อวาน';
  return fmtDay.format(new Date(`${key}T12:00:00+07:00`));
}
function timeText(iso) {
  return Date.now() - Date.parse(iso) < 86400000 ? ago(iso) : fmtTime.format(new Date(iso));
}

// ---------- state + URL ----------
const params = new URLSearchParams(location.search);
const state = {
  data: null,
  error: null,
  q: params.get('q') || '',
  cat: params.get('cat') || '',
  company: params.get('company') || '',
  imp: params.get('imp') === '1',
  sort: params.get('sort') === 'important' ? 'important' : 'latest',
  shown: PAGE,
};

function syncUrl() {
  const p = new URLSearchParams();
  if (state.q) p.set('q', state.q);
  if (state.cat) p.set('cat', state.cat);
  if (state.company) p.set('company', state.company);
  if (state.imp) p.set('imp', '1');
  if (state.sort !== 'latest') p.set('sort', state.sort);
  const qs = p.toString();
  history.replaceState(null, '', qs ? `?${qs}` : location.pathname);
}

function setFilter(patch) {
  Object.assign(state, patch, { shown: PAGE });
  syncUrl();
  render();
}

function matches(it, skip) {
  if (skip !== 'cat' && state.cat && it.category !== state.cat) return false;
  if (state.company && !(it.companies || []).includes(state.company)) return false;
  if (state.imp && (it.importance || 0) < 4) return false;
  if (state.q) {
    const hay = [it.title_th, it.title, it.summary_th, it.source, ...(it.companies || [])].join(' ').toLowerCase();
    if (!state.q.toLowerCase().split(/\s+/).filter(Boolean).every((w) => hay.includes(w))) return false;
  }
  return true;
}

function visibleItems() {
  const list = state.data.items.filter((it) => matches(it));
  if (state.sort === 'important') {
    list.sort((a, b) => (b.importance || 0) - (a.importance || 0) || b.published.localeCompare(a.published));
  }
  return list;
}

// ---------- render ----------
function renderHeader() {
  const d = state.data;
  $('#updated').innerHTML = d ? `อัปเดต <span title="${esc(fmtFull.format(new Date(d.updated_at)))}">${esc(ago(d.updated_at))}</span>` : '';
  if (d) {
    const ok = d.sources.filter((s) => s.ok).length;
    $('#subtitle').textContent = `รวมข่าวจาก ${num(ok)} แหล่ง สรุปเป็นภาษาไทยด้วย Gemini · เก็บย้อนหลัง 14 วัน`;
    $('#model').textContent = d.model ? `โมเดล ${d.model}` : 'ยังไม่ได้ใช้ Gemini';
  }
}

function renderAlert() {
  const box = $('#alert');
  if (state.error) {
    box.innerHTML = `<div class="alert alert-error" role="alert">${ICON.error}
      <div class="alert-body"><div class="alert-title">โหลดข่าวไม่สำเร็จ</div>
      <div class="alert-text">${esc(state.error)} — ตรวจว่ามีไฟล์ data/news.json และเปิดหน้านี้ผ่านเว็บเซิร์ฟเวอร์ (ไม่ใช่เปิดไฟล์ตรงๆ)</div></div>
      <button class="btn btn-outline btn-sm" type="button" data-action="retry">ลองใหม่</button></div>`;
    return;
  }
  const d = state.data;
  const notes = [];
  if (d && d.items.length && !d.items.some((it) => it.ai)) {
    notes.push(['ยังไม่ได้สรุปด้วย Gemini', 'ตอนนี้แสดงคำโปรยต้นฉบับ ตั้ง GEMINI_API_KEY ใน GitHub Secrets แล้วรอบถัดไปจะสรุปให้ย้อนหลัง']);
  }
  const ageH = d ? (Date.now() - Date.parse(d.updated_at)) / 3600000 : 0;
  if (ageH > STALE_HOURS) {
    notes.push(['ข้อมูลไม่ได้อัปเดตนานแล้ว', `อัปเดตล่าสุด ${fmtFull.format(new Date(d.updated_at))} ตรวจหน้า Actions ของ repo ว่างานอัปเดตข่าวล้มหรือถูกปิดไว้`]);
  }
  box.innerHTML = notes.map(([t, m]) => `<div class="alert alert-warning">${ICON.alert}
    <div class="alert-body"><div class="alert-title">${esc(t)}</div><div class="alert-text">${esc(m)}</div></div></div>`).join('');
}

function renderDigest() {
  const el = $('#digest');
  const dg = state.data?.digest;
  if (!dg || !dg.headline) { el.hidden = true; return; }
  const byId = new Set(state.data.items.map((it) => it.id));
  const points = (dg.points || []).map((p) => {
    const ids = (p.ids || []).filter((id) => byId.has(id));
    const links = ids.slice(0, 3).map((id, i) => `<button class="ref-link" type="button" data-goto="${esc(id)}">ดูข่าว${ids.length > 1 ? ` ${i + 1}` : ''}</button>`).join('');
    return `<li>${esc(p.text)}${links}</li>`;
  }).join('');
  el.innerHTML = `<div class="digest-head"><h2>สรุปข่าวเด่น</h2>
      <span class="badge badge-muted">สร้างโดย AI</span>
      <span class="muted" style="font-size:12px" title="${esc(fmtFull.format(new Date(dg.generated_at)))}">${esc(ago(dg.generated_at))}</span></div>
    <p class="digest-headline">${esc(dg.headline)}</p>
    ${points ? `<ol>${points}</ol>` : ''}`;
  el.hidden = false;
}

function renderCats() {
  const counts = {};
  let total = 0;
  for (const it of state.data.items) {
    if (!matches(it, 'cat')) continue;
    counts[it.category] = (counts[it.category] || 0) + 1;
    total += 1;
  }
  const chip = (value, label, n) => `<button class="chip" type="button" data-cat="${esc(value)}" aria-pressed="${state.cat === value}">${esc(label)} <span class="numeral">${num(n)}</span></button>`;
  $('#cats').innerHTML = chip('', 'ทั้งหมด', total) + CATEGORIES.filter((c) => counts[c] || state.cat === c).map((c) => chip(c, c, counts[c] || 0)).join('');
}

function renderActiveFilter(count) {
  const parts = [];
  if (state.company) parts.push(`บริษัท <strong>${esc(state.company)}</strong>`);
  if (state.q) parts.push(`คำค้น “${esc(state.q)}”`);
  const filtered = parts.length || state.cat || state.imp;
  $('#active-filter').innerHTML = filtered
    ? `<div class="filter-note">พบ <span class="numeral">${num(count)}</span> ข่าว${parts.length ? ` · ${parts.join(' · ')}` : ''}
       <button class="btn btn-ghost btn-sm" type="button" data-action="clear">ล้างตัวกรอง</button></div>`
    : '';
}

function card(it) {
  const badges = [];
  if (it.importance >= 5) badges.push('<span class="badge badge-default">สำคัญมาก</span>');
  else if (it.importance >= 4) badges.push('<span class="badge badge-default">สำคัญ</span>');
  badges.push(it.ai ? `<span class="badge badge-outline">${esc(it.category)}</span>` : '<span class="badge badge-muted">ยังไม่ได้สรุป</span>');
  const orig = it.ai && it.title && it.title !== it.title_th ? `<p class="news-orig">${esc(it.title)}</p>` : '';
  const tags = (it.companies || []).map((c) => `<button class="tag-btn" type="button" data-company="${esc(c)}">${esc(c)}</button>`).join('');
  return `<article class="card news" id="n-${esc(it.id)}">
    <div class="news-meta">${badges.join('')}<span>${esc(it.source)}</span><span class="dot"></span>
      <time datetime="${esc(it.published)}" title="${esc(fmtFull.format(new Date(it.published)))}">${esc(timeText(it.published))}</time></div>
    <h3><a href="${esc(safeUrl(it.url))}" target="_blank" rel="noopener noreferrer">${esc(it.title_th || it.title)}${ICON.ext}</a></h3>
    ${orig}
    ${it.summary_th ? `<p class="news-summary">${esc(it.summary_th)}</p>` : ''}
    ${tags ? `<div class="news-tags">${tags}</div>` : ''}
  </article>`;
}

function renderFeed() {
  const list = visibleItems();
  renderActiveFilter(list.length);
  const feed = $('#feed');
  if (!state.data.items.length) {
    feed.innerHTML = `<div class="empty">${ICON.inbox}<div class="empty-title">ยังไม่มีข่าว</div>
      <div class="empty-hint">รองานอัปเดตอัตโนมัติรอบแรก หรือรัน python scripts/fetch_news.py เพื่อดึงข่าวทันที</div></div>`;
    $('#more').innerHTML = '';
    return;
  }
  if (!list.length) {
    feed.innerHTML = `<div class="empty">${ICON.search}<div class="empty-title">ไม่พบข่าวที่ตรงกับตัวกรอง</div>
      <div class="empty-hint">ลองคำค้นที่สั้นลง หรือเลือกหมวด "ทั้งหมด"</div>
      <div style="margin-top:12px"><button class="btn btn-outline" type="button" data-action="clear">ล้างตัวกรอง</button></div></div>`;
    $('#more').innerHTML = '';
    return;
  }
  const page = list.slice(0, state.shown);
  let html = '';
  let lastDay = '';
  for (const it of page) {
    if (state.sort === 'latest') {
      const key = dayKey(new Date(it.published));
      if (key !== lastDay) { html += `<h2 class="day-head">${esc(dayLabel(key))}</h2>`; lastDay = key; }
    }
    html += card(it);
  }
  feed.innerHTML = html;
  $('#more').innerHTML = list.length > state.shown
    ? `<span>แสดง <span class="numeral">${num(page.length)}</span> จาก <span class="numeral">${num(list.length)}</span> ข่าว</span>
       <button class="btn btn-outline" type="button" data-action="more">แสดงเพิ่ม</button>`
    : '';
}

function renderSide() {
  const counts = new Map();
  for (const it of state.data.items) for (const c of it.companies || []) counts.set(c, (counts.get(c) || 0) + 1);
  const top = [...counts].sort((a, b) => b[1] - a[1]).slice(0, 18);
  $('#companies').innerHTML = top.length
    ? top.map(([c, n]) => `<button class="chip" type="button" data-company="${esc(c)}" aria-pressed="${state.company === c}">${esc(c)} <span class="numeral">${num(n)}</span></button>`).join('')
    : '<p class="hint" style="margin:0">จะแสดงหลังสรุปด้วย Gemini</p>';
  $('#sources').innerHTML = state.data.sources.map((s) => `<li title="${esc(s.error || '')}"><span class="name">${esc(s.name)}</span>
    ${s.ok ? `<span class="badge badge-success"><span class="numeral">${num(s.count)}</span> ข่าว</span>` : '<span class="badge badge-destructive">ดึงไม่ได้</span>'}</li>`).join('');
}

function renderLoading() {
  $('#feed').innerHTML = Array.from({ length: 5 }, () => '<div class="skeleton" style="height:118px"></div>').join('');
}

function render() {
  renderHeader();
  renderAlert();
  if (!state.data) { if (!state.error) renderLoading(); else $('#feed').innerHTML = ''; return; }
  renderDigest();
  renderCats();
  renderFeed();
  renderSide();
  document.querySelectorAll('.segmented button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.sort === state.sort)));
  $('#imp').checked = state.imp;
  if ($('#q').value !== state.q) $('#q').value = state.q;
}

// ---------- data ----------
async function load() {
  const btn = $('#reload');
  btn.classList.add('spinning');
  if (state.data) $('#feed').style.opacity = '0.6';
  try {
    const res = await fetch(`data/news.json?t=${Date.now()}`, { cache: 'no-store' });
    if (!res.ok) throw new Error(`เซิร์ฟเวอร์ตอบ ${res.status}`);
    state.data = await res.json();
    state.error = null;
  } catch (err) {
    state.error = err instanceof Error ? err.message : String(err);
  } finally {
    btn.classList.remove('spinning');
    $('#feed').style.opacity = '';
    render();
  }
}

function gotoItem(id) {
  Object.assign(state, { q: '', cat: '', company: '', imp: false, sort: 'latest' });
  const idx = visibleItems().findIndex((it) => it.id === id);
  if (idx < 0) return;
  state.shown = Math.max(state.shown, Math.ceil((idx + 1) / PAGE) * PAGE);
  syncUrl();
  render();
  const el = document.getElementById(`n-${id}`);
  if (!el) return;
  el.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
  el.classList.add('flash');
  setTimeout(() => el.classList.remove('flash'), 1600);
}

// ---------- events ----------
document.addEventListener('click', (e) => {
  const t = e.target.closest('button');
  if (!t) return;
  if (t.dataset.cat !== undefined) setFilter({ cat: state.cat === t.dataset.cat ? '' : t.dataset.cat });
  else if (t.dataset.company !== undefined) setFilter({ company: state.company === t.dataset.company ? '' : t.dataset.company });
  else if (t.dataset.sort) setFilter({ sort: t.dataset.sort });
  else if (t.dataset.goto) gotoItem(t.dataset.goto);
  else if (t.dataset.action === 'more') { state.shown += PAGE; renderFeed(); }
  else if (t.dataset.action === 'clear') setFilter({ q: '', cat: '', company: '', imp: false });
  else if (t.dataset.action === 'retry' || t.id === 'reload') load();
});

let searchTimer;
$('#q').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => setFilter({ q: e.target.value.trim() }), 300);
});
$('#imp').addEventListener('change', (e) => setFilter({ imp: e.target.checked }));

// แผงบริษัท/แหล่งข่าว: เปิดค้างบนจอกว้าง พับไว้บนจอแคบ และตามทันเมื่อหมุนจอ/พับจอ
const wide = matchMedia('(min-width: 1024px)');
const syncSide = () => { $('#side-details').open = wide.matches; };
wide.addEventListener('change', syncSide);
syncSide();

// เวลา "x นาทีที่แล้ว" ให้เดินเอง และดึงข้อมูลใหม่ทุก 15 นาทีถ้าเปิดหน้าทิ้งไว้
setInterval(() => { if (state.data) renderHeader(); }, 60000);
setInterval(() => { if (document.visibilityState === 'visible') load(); }, 15 * 60000);

$('#q').value = state.q;
render();
load();
