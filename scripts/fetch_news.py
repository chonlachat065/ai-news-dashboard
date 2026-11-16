"""ดึงข่าว AI จากแหล่งใน feeds.json → สรุปภาษาไทยด้วย Gemini → เขียน site/data/news.json

รัน:      GEMINI_API_KEY=xxx python scripts/fetch_news.py
ไม่มี key: ยังดึงข่าวได้ แต่ใช้คำโปรยต้นฉบับแทนสรุป (ไว้ทดสอบ) — รอบถัดไปที่มี key จะสรุปให้ย้อนหลัง
ใช้แค่ standard library ไม่ต้อง pip install
"""
import collections
import concurrent.futures
import gzip
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEEDS_FILE = ROOT / "feeds.json"
OUT_FILE = ROOT / "site" / "data" / "news.json"

def env(name, default, cast=str):
    """ค่าว่าง = ใช้ค่าเริ่มต้น (GitHub Actions ส่ง "" มาเมื่อไม่ได้ตั้ง Variable)"""
    value = os.environ.get(name, "").strip()
    return cast(value) if value else default


API_KEY = env("GEMINI_API_KEY", "")
MODEL_ENV = env("GEMINI_MODEL", "")
MAX_AGE_DAYS = env("MAX_AGE_DAYS", 3, int)          # ข่าวเก่ากว่านี้ไม่รับเข้า
KEEP_DAYS = env("KEEP_DAYS", 14, int)               # เก็บไว้ในไฟล์กี่วัน
MAX_NEW = env("MAX_NEW_PER_RUN", 150, int)          # ส่งให้ Gemini สรุปได้สูงสุดกี่ข่าวต่อรอบ (คุมงบ)
BATCH = env("BATCH_SIZE", 15, int)                  # กี่ข่าวต่อการเรียก API หนึ่งครั้ง
CALL_GAP = env("CALL_GAP_SEC", 5.0, float)          # เว้นระหว่างการเรียก (free tier จำกัดครั้ง/นาที)

DATA_VERSION = 3  # 2 = แตกการ์ดที่รวมผิดของรุ่น 1 · 3 = ตรวจข่าวซ้ำ 4 วันล่าสุดใหม่หลังขยายขนาดกลุ่ม (ทำครั้งเดียว)

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
FALLBACK_MODEL = "gemini-2.5-flash-lite"
UA = "Mozilla/5.0 (compatible; ai-news-dashboard/1.0)"
CATEGORIES = [
    "โมเดลใหม่",
    "ผลิตภัณฑ์/ฟีเจอร์",
    "งานวิจัย",
    "ธุรกิจ/การลงทุน",
    "นโยบาย/กฎหมาย",
    "เครื่องมือ/โอเพนซอร์ส",
    "อื่นๆ",
]

stats = {"calls": 0, "in_tokens": 0, "out_tokens": 0}
model = None


def log(msg):
    print(msg, flush=True)


# ---------- ดึงข่าว ----------

def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    # บางเซิร์ฟเวอร์ (เช่น DeepMind) ส่ง gzip มาแม้ไม่ได้ขอ
    return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw


def local(tag):
    return tag.rsplit("}", 1)[-1]


def child(el, *names):
    for c in el:
        if local(c.tag) in names:
            return c
    return None


def child_text(el, *names):
    for c in el:
        if local(c.tag) in names and c.text and c.text.strip():
            return c.text.strip()
    return ""


def entry_link(el):
    links = [c for c in el if local(c.tag) == "link"]
    for c in links:  # Atom: <link rel="alternate" href="...">
        if c.get("href") and c.get("rel", "alternate") == "alternate":
            return c.get("href")
    for c in links:  # RSS: <link>...</link>
        if c.text and c.text.strip():
            return c.text.strip()
        if c.get("href"):
            return c.get("href")
    guid = child_text(el, "guid", "id")
    return guid if guid.startswith("http") else ""


def parse_date(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def clean_text(s, limit=600):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"\s+", " ", html.unescape(s)).strip()
    return s[:limit]


def normalize_url(url):
    p = urllib.parse.urlsplit(url.strip())
    query = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not k.lower().startswith(("utm_", "ref", "fbclid"))]
    return urllib.parse.urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/"), urllib.parse.urlencode(query), ""))


def item_id(url):
    return hashlib.sha1(normalize_url(url).encode()).hexdigest()[:12]


def title_key(title):
    return re.sub(r"\W+", "", title.lower())[:80]


def parse_rss(raw, feed):
    root = ET.fromstring(raw)
    out = []
    for el in root.iter():
        if local(el.tag) not in ("item", "entry"):
            continue
        title = clean_text(child_text(el, "title"), 300)
        url = entry_link(el)
        if not title or not url:
            continue
        src = child(el, "source")  # Google News บอกสำนักข่าวจริงไว้ใน <source>
        source = src.text.strip() if src is not None and src.text and "Google News" in feed["name"] else feed["name"]
        if source in feed.get("exclude_sources", []):
            continue
        if source != feed["name"]:
            title = re.sub(r"\s+-\s+" + re.escape(source) + r"$", "", title)
        body = clean_text(child_text(el, "description", "summary", "content", "encoded"))
        if body.startswith(title):  # Google News ใส่ "หัวข้อ + ชื่อสำนักข่าว" ไว้ในคำโปรย ไม่มีเนื้อหาเพิ่ม
            body = body[len(title):].strip(" -–|")
            if body in (source, feed["name"]):
                body = ""
        date = parse_date(child_text(el, "pubDate", "published", "updated", "date"))
        out.append({"title": title, "url": url, "source": source, "text": body, "published": date})
    return out


def parse_hf_papers(raw, feed):
    out = []
    for row in json.loads(raw):
        paper = row.get("paper", {})
        pid = paper.get("id")
        if not pid:
            continue
        out.append({
            "title": clean_text(row.get("title") or paper.get("title"), 300),
            "url": f"https://huggingface.co/papers/{pid}",
            "source": feed["name"],
            "text": clean_text(paper.get("summary")),
            "published": parse_date(row.get("publishedAt") or paper.get("publishedAt")),
            "_rank": paper.get("upvotes", 0),
        })
    out.sort(key=lambda x: x["_rank"], reverse=True)
    return out


def parse_hn(raw, feed):
    out = []
    for h in json.loads(raw).get("hits", []):
        hn_url = f"https://news.ycombinator.com/item?id={h['objectID']}"
        out.append({
            "title": clean_text(h.get("title"), 300),
            "url": h.get("url") or hn_url,
            "source": feed["name"],
            "text": f"{h.get('points', 0)} points, {h.get('num_comments', 0)} comments on Hacker News ({hn_url})",
            "published": parse_date(h.get("created_at")),
            "_rank": h.get("points", 0),
        })
    out.sort(key=lambda x: x["_rank"], reverse=True)
    return out


def parse_openrouter(raw, feed):
    """โมเดลที่เพิ่งเปิดให้ใช้บน OpenRouter — สัญญาณเร็วสุดว่ามีโมเดลใหม่ออก"""
    out, seen = [], set()
    for m in sorted(json.loads(raw).get("data", []), key=lambda m: m.get("created", 0), reverse=True):
        base = m.get("id", "").split(":")[0]  # รวม :free / :batch / :thinking เป็นตัวเดียว
        if not base or base in seen:
            continue
        seen.add(base)
        name = re.sub(r"\s*\((free|batch|thinking|beta)\)$", "", m.get("name") or base)
        ctx = m.get("context_length")
        out.append({
            "title": f"New model on OpenRouter: {name}",
            "url": f"https://openrouter.ai/{base}",
            "source": feed["name"],
            "text": clean_text(m.get("description")) + (f" (context {ctx:,} tokens)" if ctx else ""),
            "published": datetime.fromtimestamp(m.get("created", 0), timezone.utc),
        })
    return out


def parse_hf_models(raw, feed):
    """โมเดลที่กำลังมาแรงบน Hugging Face (เรียงตาม trending) เฉพาะที่มีคนกดถูกใจถึงเกณฑ์"""
    out = []
    for m in json.loads(raw):
        if m.get("likes", 0) < feed.get("min_likes", 100):
            continue
        out.append({
            "title": f"Trending on Hugging Face: {m['id']}",
            "url": f"https://huggingface.co/{m['id']}",
            "source": feed["name"],
            "text": f"{m.get('pipeline_tag') or 'model'} · {m.get('likes', 0):,} likes · trending score {m.get('trendingScore', 0)}",
            "published": parse_date(m.get("createdAt")),
        })
    return out


def fetch_feed(feed):
    kind, url = feed.get("type", "rss"), feed["url"]
    if kind == "hn":  # Hacker News ผ่าน Algolia API: เฉพาะเรื่องที่คะแนนถึงเกณฑ์ใน MAX_AGE_DAYS วันล่าสุด
        since = int(time.time()) - MAX_AGE_DAYS * 86400
        url += f"&hitsPerPage=50&numericFilters=points>{feed.get('min_points', 100)},created_at_i>{since}"
    raw = http_get(url)
    parser = {"hf_papers": parse_hf_papers, "hn": parse_hn, "openrouter": parse_openrouter,
              "hf_models": parse_hf_models}.get(kind, parse_rss)
    items = parser(raw, feed)
    if kind == "rss":  # Google News เรียงตามความเกี่ยวข้อง ไม่ใช่วันที่ → เรียงใหม่ก่อนตัด ไม่งั้นได้ข่าวเก่า
        oldest = datetime.min.replace(tzinfo=timezone.utc)
        items.sort(key=lambda x: x["published"] or oldest, reverse=True)
    return items[: feed.get("limit", 10)]


# ---------- Gemini ----------

def gemini(path, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API_BASE}/{path}",
        data=data,
        headers={"x-goog-api-key": API_KEY, "Content-Type": "application/json"},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def pick_model():
    """ไม่ได้ตั้ง GEMINI_MODEL → เลือก flash-lite รุ่นใหม่สุดที่ไม่ใช่ preview (ถูกสุดและพอสำหรับงานสรุป)"""
    if MODEL_ENV:
        return MODEL_ENV
    try:
        names = []
        for m in gemini("models?pageSize=1000").get("models", []):
            name = m["name"].split("/", 1)[-1]
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            if "flash-lite" in name and not re.search(r"preview|exp|tts|image|live|audio|latest", name):
                names.append(name)

        def version(n):
            v = re.search(r"gemini-(\d+)(?:\.(\d+))?", n)
            return (int(v.group(1)), int(v.group(2) or 0)) if v else (0, 0)

        if names:
            return max(names, key=lambda n: (version(n), -len(n)))
    except Exception as e:  # noqa: BLE001 — เลือกรุ่นไม่ได้ก็ใช้ค่าสำรอง
        log(f"  ! ดูรายชื่อโมเดลไม่ได้ ({e}) ใช้ {FALLBACK_MODEL}")
    return FALLBACK_MODEL


def generate(prompt, schema):
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json", "responseSchema": schema},
    }
    for attempt in range(4):
        try:
            stats["calls"] += 1
            res = gemini(f"models/{model}:generateContent", body)
            usage = res.get("usageMetadata", {})
            stats["in_tokens"] += usage.get("promptTokenCount", 0)
            stats["out_tokens"] += usage.get("candidatesTokenCount", 0)
            text = "".join(p.get("text", "") for p in res["candidates"][0]["content"]["parts"])
            return json.loads(text)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code in (429, 500, 503) and attempt < 3:
                wait = 20 * (attempt + 1)
                log(f"  ! Gemini {e.code} รอ {wait} วิแล้วลองใหม่")
                time.sleep(wait)
                continue
            raise RuntimeError(f"Gemini HTTP {e.code}: {detail}") from e
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            if attempt < 1:
                time.sleep(CALL_GAP)
                continue
            raise RuntimeError(f"อ่านคำตอบ Gemini ไม่ได้: {e}") from e
    raise RuntimeError("Gemini ไม่ตอบหลังลองครบ")


SUMMARY_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "STRING"},
            "relevant": {"type": "BOOLEAN"},
            "title_th": {"type": "STRING"},
            "summary_th": {"type": "STRING"},
            "category": {"type": "STRING", "enum": CATEGORIES},
            "companies": {"type": "ARRAY", "items": {"type": "STRING"}},
            "importance": {"type": "INTEGER"},
        },
        "required": ["id", "relevant", "title_th", "summary_th", "category", "companies", "importance"],
    },
}

SUMMARY_PROMPT = """คุณคือบรรณาธิการข่าว AI สำหรับคนทำงานด้าน AI ในไทย
สำหรับข่าวแต่ละชิ้นด้านล่าง ให้ตอบเป็นรายการ JSON ชิ้นละหนึ่งรายการ:
- id: ตามที่ให้มา
- relevant: true ถ้าเป็นข่าวเกี่ยวกับ AI / ML / LLM รวมถึงโมเดลใหม่ (แม้เป็นแค่ชื่อโมเดลบน OpenRouter หรือ Hugging Face)
  ธุรกิจ หุ้น การลงทุน คดี และระบบล่มของบริษัท AI · false เฉพาะข่าวที่ไม่เกี่ยวกับ AI เลย, สแปม, หรือโฆษณา
- title_th: หัวข้อภาษาไทยกระชับ ไม่เกิน 90 ตัวอักษร คงชื่อเฉพาะและชื่อโมเดลเป็นภาษาอังกฤษ
- summary_th: สรุปภาษาไทย 2–3 ประโยค บอกว่าเกิดอะไรขึ้นและสำคัญอย่างไร ใช้เฉพาะข้อมูลที่มีในต้นฉบับ ห้ามเติมตัวเลขหรือข้อเท็จจริงเอง
- category: เลือกหนึ่งหมวดที่ตรงที่สุด
- companies: บริษัทหรือองค์กรหลักในข่าว (ชื่อภาษาอังกฤษ) ไม่เกิน 3 ชื่อ ไม่มีให้เป็นรายการว่าง
- importance: 1–5 · 5 = เปลี่ยนวงการ (โมเดลเรือธงใหม่จากแล็บใหญ่, ดีลหรือกฎหมายระดับโลก) · 3 = คนในวงการควรรู้ · 1 = เรื่องเล็กหรือเฉพาะกลุ่ม

ข่าว:
"""

DIGEST_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "headline": {"type": "STRING"},
        "points": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"text": {"type": "STRING"}, "ids": {"type": "ARRAY", "items": {"type": "STRING"}}},
                "required": ["text", "ids"],
            },
        },
    },
    "required": ["headline", "points"],
}

DIGEST_PROMPT = """จากข่าว AI ด้านล่าง (เรียงตามความสำคัญ) เขียนสรุปประจำวันภาษาไทยสำหรับคนทำงานด้าน AI:
- headline: หนึ่งประโยค บอกภาพรวมที่สำคัญที่สุดของวันนี้
- points: 3–5 ข้อ ข้อละ 1–2 ประโยค รวมข่าวเรื่องเดียวกันไว้ข้อเดียว พร้อม ids ของข่าวที่อ้างถึง
ใช้เฉพาะข้อมูลที่ให้มา ห้ามแต่งเพิ่ม

ข่าว:
"""


DEDUP_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "groups": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"event": {"type": "STRING"}, "ids": {"type": "ARRAY", "items": {"type": "STRING"}}},
                "required": ["event", "ids"],
            },
        },
    },
    "required": ["groups"],
}

DEDUP_PROMPT = """ด้านล่างคือรายการข่าว AI (id, หัวข้อ, หมวด, แหล่ง, วันที่) บางชิ้นเป็นข่าวเดียวกันที่รายงานจากคนละสำนัก
จับกลุ่มเฉพาะข่าวที่รายงาน "เหตุการณ์เดียวกันเป๊ะ" เช่น ประกาศเปิดตัวโมเดลตัวเดียวกัน ดีลเดียวกัน คำตัดสินคดีเดียวกัน
กติกา:
- ห้ามรวมข่าวที่แค่พูดถึงบริษัท บุคคล หรือเรื่องราวต่อเนื่องเดียวกัน แต่เป็นคนละเหตุการณ์
  (เช่น "บริษัท A เปิดตัวโมเดล X" กับ "บริษัท A เข้าตลาดหุ้น" เป็นคนละข่าว · ข่าวความคืบหน้าแต่ละวันของเรื่องเดียวกันเป็นคนละข่าว)
- ข่าวเปิดตัวผลิตภัณฑ์หรือโมเดลต่างตัวกัน ห้ามรวมกันเด็ดขาด
- event: เขียนเหตุการณ์ที่ทุกข่าวในกลุ่มรายงานตรงกันในประโยคเดียว ถ้าเขียนให้เป็นเหตุการณ์เดียวไม่ได้ แปลว่าไม่ควรรวม
- ถ้าไม่แน่ใจให้แยกไว้ ตอบเฉพาะกลุ่มที่มีตั้งแต่ 2 ข่าวขึ้นไป

ข่าว:
"""
DEDUP_MAX_GROUP = 15         # กลุ่มใหญ่กว่านี้มักเป็น "หัวข้อเดียวกัน" ไม่ใช่ "ข่าวเดียวกัน" → ไม่รวม
DEDUP_MAX_ALSO = 10          # การ์ดหนึ่งเก็บลิงก์แหล่งอื่นได้ไม่เกินนี้ (ส่วนเกินยังถูกรวม แค่ไม่เก็บลิงก์)
DEDUP_WINDOW = timedelta(hours=48)


def dedupe(items, now):
    """ให้ Gemini จับข่าวเหตุการณ์เดียวกันจากหลายสำนัก แล้วรวมเป็นการ์ดเดียว · คืน id ของข่าวที่ถูกรวมเข้าการ์ดอื่น"""
    fresh = [it for it in items.values() if it["ai"] and not it.get("dedup")]
    if not fresh:
        return []
    since = (now - timedelta(days=MAX_AGE_DAYS + 1)).isoformat()
    ref = sorted((it for it in items.values() if it["ai"] and it.get("dedup") and it["published"] >= since),
                 key=lambda it: it["published"], reverse=True)[:150]
    pool = ref + fresh
    payload = [{"id": it["id"], "title": it["title_th"], "category": it["category"], "source": it["source"],
                "date": it["published"][:10]} for it in pool]
    groups = generate(DEDUP_PROMPT + json.dumps(payload, ensure_ascii=False), DEDUP_SCHEMA).get("groups", [])

    fresh_ids = {it["id"] for it in fresh}
    touched, removed = set(), []
    for g in groups:
        # ไม่รวมต่อทอด: ข่าวที่อยู่ในกลุ่มอื่นของรอบนี้แล้วไม่นับซ้ำ (เดิมการต่อทอดทำให้การ์ดเดียวกลืนข่าว 30 ชิ้น)
        ids = [i for i in dict.fromkeys(g.get("ids", [])) if i in items and i not in touched]
        if not 2 <= len(ids) <= DEDUP_MAX_GROUP or not fresh_ids.intersection(ids):
            continue
        group = [items[i] for i in ids]
        # หมวดต่างกันมักแปลว่ารวมผิด (เช่นข่าวเปิดตัวโมเดลกับข่าวหุ้น) → เก็บเฉพาะหมวดที่มากสุดในกลุ่ม
        top_cat = collections.Counter(it["category"] for it in group).most_common(1)[0][0]
        group = [it for it in group if it["category"] == top_cat]
        # การ์ดเดิมที่แสดงอยู่แล้วเป็นหลัก ไม่งั้นเลือกข่าวที่สำคัญสุด แล้วข่าวที่ออกก่อน (มักเป็นต้นทาง)
        group.sort(key=lambda it: (not it.get("dedup"), -it["importance"], it["published"]))
        primary = group[0]
        t0 = datetime.fromisoformat(primary["published"])
        group = [primary] + [it for it in group[1:] if abs(datetime.fromisoformat(it["published"]) - t0) <= DEDUP_WINDOW]
        if len(group) < 2:
            continue
        also = primary.setdefault("also", [])
        seen = {primary["url"]} | {a["url"] for a in also}
        for it in group[1:]:
            for a in [{"source": it["source"], "url": it["url"]}, *it.get("also", [])]:
                if a["url"] not in seen and len(also) < DEDUP_MAX_ALSO:
                    seen.add(a["url"])
                    also.append(a)
            primary["importance"] = max(primary["importance"], it["importance"])
            primary["companies"] = list(dict.fromkeys(primary["companies"] + it["companies"]))[:4]
            del items[it["id"]]
            removed.append(it["id"])
        touched.update(it["id"] for it in group)
    for it in fresh:
        if it["id"] in items:
            it["dedup"] = True
    return removed


def summarize(batch):
    payload = [{"id": it["id"], "source": it["source"], "title": it["title"], "text": it.get("text", "")[:500]} for it in batch]
    rows = generate(SUMMARY_PROMPT + json.dumps(payload, ensure_ascii=False), SUMMARY_SCHEMA)
    return {r["id"]: r for r in rows if isinstance(r, dict) and r.get("id")}


def make_digest(items, now):
    recent = [it for it in items if it["ai"] and it["published"] >= (now - timedelta(hours=24)).isoformat()]
    if len(recent) < 5:
        recent = [it for it in items if it["ai"]][:40]
    recent.sort(key=lambda it: it["importance"], reverse=True)
    payload = [{"id": it["id"], "title": it["title_th"], "summary": it["summary_th"]} for it in recent[:25]]
    if not payload:
        return None
    d = generate(DIGEST_PROMPT + json.dumps(payload, ensure_ascii=False), DIGEST_SCHEMA)
    known = {it["id"] for it in recent}
    points = [{"text": p["text"], "ids": [i for i in p.get("ids", []) if i in known]} for p in d.get("points", [])]
    return {"generated_at": now.isoformat(), "headline": d.get("headline", ""), "points": points}


# ---------- main ----------

def main():
    global model
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    feeds = json.loads(FEEDS_FILE.read_text(encoding="utf-8"))["feeds"]
    old = json.loads(OUT_FILE.read_text(encoding="utf-8")) if OUT_FILE.exists() else {}
    items = {it["id"]: it for it in old.get("items", [])}
    skipped = list(old.get("skipped_ids", []))
    version = old.get("version", 1) if old else DATA_VERSION
    max_new, lookback = MAX_NEW, MAX_AGE_DAYS
    if API_KEY and version < 2:
        # รุ่น 1 รวมข่าวผิดเยอะ (ข่าวโมเดลใหม่ถูกกลืนเข้าการ์ดอื่น) → แตกการ์ดที่รวมไว้ ปล่อยให้ข่าวที่ถูกกลืนถูกดึงกลับมา
        # แล้วตรวจข่าวซ้ำใหม่ทั้งหมดด้วยกติกาที่เข้มขึ้น · รอบนี้ย้อนหลัง 7 วันและสรุปได้มากกว่าปกติ
        restore = {item_id(a["url"]) for it in items.values() for a in it.pop("also", [])}
        skipped = [i for i in skipped if i not in restore]
        for it in items.values():
            it["dedup"] = False
        max_new, lookback, version = 400, 7, 2
        log(f"ย้ายข้อมูลเป็นรุ่น 2: แตกการ์ดที่รวมไว้ {len(restore)} ลิงก์")
    if API_KEY and version < 3:
        # รุ่น 2 ไม่รวมกลุ่มเกิน 6 ข่าว ข่าวใหญ่ที่ทุกสำนักลง (เช่นเปิดตัว Qwen4-Max) จึงขึ้นซ้ำหลายการ์ด → ตรวจ 4 วันล่าสุดใหม่
        recent = (now - timedelta(days=4)).isoformat()
        for it in items.values():
            if it["published"] >= recent:
                it["dedup"] = False
        version = 3
        log("ย้ายข้อมูลเป็นรุ่น 3: ตรวจข่าวซ้ำ 4 วันล่าสุดใหม่")
    skipped_set = set(skipped)
    titles = {title_key(it["title"]) for it in items.values()}

    log(f"ดึงข่าวจาก {len(feeds)} แหล่ง…")
    sources, fresh = [], []
    cutoff = now - timedelta(days=lookback)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_feed, f): f for f in feeds}
        for fut in concurrent.futures.as_completed(futures):
            feed = futures[fut]
            try:
                got = fut.result()
                sources.append({"name": feed["name"], "ok": True, "count": len(got), "error": None})
                fresh.extend(got)
                log(f"  ✓ {feed['name']}: {len(got)}")
            except Exception as e:  # noqa: BLE001 — แหล่งเดียวพังไม่ควรล้มทั้งรอบ
                sources.append({"name": feed["name"], "ok": False, "count": 0, "error": str(e)[:160]})
                log(f"  ✗ {feed['name']}: {e}")
    order = {f["name"]: i for i, f in enumerate(feeds)}
    sources.sort(key=lambda s: order.get(s["name"], 99))

    added = 0
    for it in fresh:
        pub = min(it["published"] or now, now)
        if pub < cutoff:
            continue
        iid, tkey = item_id(it["url"]), title_key(it["title"])
        if iid in items or iid in skipped_set or tkey in titles:
            continue
        titles.add(tkey)
        items[iid] = {
            "id": iid, "url": it["url"], "source": it["source"], "published": pub.isoformat(),
            "title": it["title"], "text": it["text"],
            "title_th": it["title"], "summary_th": it["text"][:240], "category": "อื่นๆ",
            "companies": [], "importance": 0, "ai": False,
        }
        added += 1
    log(f"ข่าวใหม่ {added} ชิ้น")

    digest = old.get("digest")
    if API_KEY:
        model = pick_model()
        pending = sorted((it for it in items.values() if not it["ai"]), key=lambda it: it["published"], reverse=True)[:max_new]
        log(f"สรุปด้วย {model}: {len(pending)} ชิ้น ({-(-len(pending) // BATCH)} ครั้ง)")
        for i in range(0, len(pending), BATCH):
            batch = pending[i : i + BATCH]
            try:
                result = summarize(batch)
            except RuntimeError as e:
                log(f"  ! ชุดที่ {i // BATCH + 1} สรุปไม่สำเร็จ จะลองใหม่รอบหน้า: {e}")
                continue
            for it in batch:
                r = result.get(it["id"])
                if not r:
                    continue
                if not r["relevant"]:
                    items.pop(it["id"])
                    skipped.append(it["id"])
                    continue
                it.update({
                    "title_th": r["title_th"].strip() or it["title"],
                    "summary_th": r["summary_th"].strip(),
                    "category": r["category"] if r["category"] in CATEGORIES else "อื่นๆ",
                    "companies": [c.strip() for c in r["companies"] if c.strip()][:3],
                    "importance": max(1, min(5, int(r["importance"]))),
                    "ai": True,
                })
            time.sleep(CALL_GAP)
        removed = []
        try:
            removed = dedupe(items, now)
            skipped.extend(removed)  # ไม่ดึงข่าวที่ถูกรวมแล้วกลับมาใหม่
            log(f"รวมข่าวซ้ำต่างสำนัก {len(removed)} ชิ้น")
        except RuntimeError as e:
            log(f"  ! รวมข่าวซ้ำไม่สำเร็จ จะลองใหม่รอบหน้า: {e}")
        if added or removed or not digest:
            time.sleep(CALL_GAP)
            try:
                digest = make_digest(sorted(items.values(), key=lambda it: it["published"], reverse=True), now) or digest
            except RuntimeError as e:
                log(f"  ! ทำสรุปประจำวันไม่สำเร็จ: {e}")
    else:
        log("ไม่มี GEMINI_API_KEY — ข้ามขั้นสรุป (แสดงคำโปรยต้นฉบับแทน)")

    keep_from = (now - timedelta(days=KEEP_DAYS)).isoformat()
    kept = sorted((it for it in items.values() if it["published"] >= keep_from), key=lambda it: it["published"], reverse=True)
    out = {
        "version": version,
        "updated_at": now.isoformat(),
        "model": model,
        "digest": digest,
        "sources": sources,
        "skipped_ids": skipped[-3000:],
        "items": kept,
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = sum(s["ok"] for s in sources)
    log(f"เสร็จ: แหล่งข่าวใช้ได้ {ok}/{len(sources)} · ข่าวในไฟล์ {len(kept)} · "
        f"เรียก Gemini {stats['calls']} ครั้ง · token เข้า {stats['in_tokens']:,} / ออก {stats['out_tokens']:,}")


if __name__ == "__main__":
    main()
