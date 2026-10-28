# ข่าววงการ AI (AI News Dashboard)

หน้าเว็บรวมข่าว AI จาก 15 แหล่ง สรุปเป็นภาษาไทยด้วย Gemini อัปเดตเองทุก 4 ชั่วโมง
**ค่าใช้จ่ายมีแค่ Gemini API** ส่วนอื่นใช้ของฟรีทั้งหมด (GitHub Actions + GitHub Pages)

```
feeds.json ──► scripts/fetch_news.py ──► Gemini (สรุปไทย/จัดหมวด/ให้คะแนน) ──► site/data/news.json ──► site/ (หน้าเว็บ static)
              (GitHub Actions ทุก 4 ชม.)                                                               (GitHub Pages)
```

## ติดตั้ง (ครั้งเดียว ~10 นาที)

1. **สร้าง repo บน GitHub** แล้ว push โฟลเดอร์นี้ขึ้นไป
   - GitHub Pages ฟรีต้องเป็น repo **public** (ข้อมูลในเว็บเป็นข่าวสาธารณะอยู่แล้ว ส่วน API key อยู่ใน Secrets ไม่หลุด)
   - ถ้าต้องการ private ให้ใช้ GitHub แบบเสียเงิน หรือ deploy ขึ้นเซิร์ฟเวอร์ทีมแทน (ดูหัวข้อด้านล่าง)
2. **ขอ Gemini API key** ที่ https://aistudio.google.com/apikey
3. ใน repo ไปที่ **Settings → Secrets and variables → Actions → New repository secret**
   ชื่อ `GEMINI_API_KEY` ใส่ key ที่ได้
4. **Settings → Pages → Build and deployment → Source** เลือก **GitHub Actions**
5. แท็บ **Actions → อัปเดตข่าว AI → Run workflow** เพื่อรันรอบแรก
   เสร็จแล้วลิงก์เว็บจะขึ้นในหน้ารันนั้น (`https://<user>.github.io/<repo>/`)

หลังจากนี้ระบบรันเองทุก 4 ชั่วโมง ไม่ต้องทำอะไรเพิ่ม

## รันในเครื่อง

```bash
# ดึงข่าว (ไม่มี key ก็รันได้ จะใช้คำโปรยต้นฉบับแทนสรุป)
GEMINI_API_KEY=xxxx python scripts/fetch_news.py

# เปิดดูหน้าเว็บ
python -m http.server 8000 --directory site
# แล้วเปิด http://localhost:8000
```

ใช้แค่ Python 3.10+ standard library ไม่ต้อง `pip install` อะไร

## ค่าใช้จ่าย Gemini

- ใช้รุ่น **flash-lite รุ่นใหม่สุดที่ไม่ใช่ preview** โดยอัตโนมัติ (ถูกสุดและพอสำหรับงานสรุป)
  ถ้าต้องการล็อกรุ่น ตั้ง **Settings → Secrets and variables → Actions → Variables** ชื่อ `GEMINI_MODEL` เช่น `gemini-2.5-flash-lite`
- ส่งข่าวทีละ 15 ชิ้นต่อการเรียกหนึ่งครั้ง ส่งแค่หัวข้อ + 500 ตัวอักษรแรก และไม่สรุปข่าวซ้ำ
- ปริมาณจริงโดยประมาณ: ข่าวใหม่ 50–100 ชิ้น/วัน → เรียก API ราว 10–20 ครั้ง/วัน (รวมสรุปประจำวัน) ·
  ทุกรอบจะพิมพ์จำนวนครั้งและ token ที่ใช้ไว้ท้าย log ของ Actions ให้ตรวจได้
- ปริมาณนี้น่าจะอยู่ใน free tier ได้ แต่ **free tier ของ Google อาจนำข้อมูลที่ส่งไปใช้ปรับปรุงบริการ** —
  สำหรับโปรเจคนี้ส่งแค่ข่าวสาธารณะจึงไม่มีปัญหา อย่านำ key ชุดนี้ไปใช้กับข้อมูลภายในบริษัท

## ปรับแต่ง

| ต้องการ | แก้ที่ |
|---|---|
| เพิ่ม/ลบแหล่งข่าว | `feeds.json` (รองรับ RSS/Atom, Hacker News, HF Daily Papers · ตัดสำนักข่าวที่ไม่ต้องการด้วย `exclude_sources`) |
| ความถี่ในการอัปเดต | `cron` ใน `.github/workflows/update-news.yml` |
| จำกัดงบต่อรอบ / ขนาดชุด | Variable `MAX_NEW_PER_RUN` (90), `BATCH_SIZE` (15), `CALL_GAP_SEC` (5) |
| เก็บข่าวย้อนหลังกี่วัน | Variable `KEEP_DAYS` (14) · รับข่าวที่อายุไม่เกิน `MAX_AGE_DAYS` (3) |

ค่าทั้งหมดตั้งที่ GitHub: **Settings → Secrets and variables → Actions** — `GEMINI_API_KEY` เป็น Secret ที่เหลือเป็น Variable
ไม่ต้องตั้งอะไรบน Vercel / GitHub Pages เพราะหน้าเว็บแค่อ่าน `data/news.json`
| หมวดข่าว / เกณฑ์ความสำคัญ | `CATEGORIES` และ `SUMMARY_PROMPT` ใน `scripts/fetch_news.py` (ถ้าแก้หมวด แก้ `CATEGORIES` ใน `site/app.js` ด้วย) |

## ขึ้นเซิร์ฟเวอร์ทีมแทน GitHub Pages

หน้าเว็บเป็นไฟล์ static ทั้งหมดใน `site/` จึงวางหลัง web server ใดก็ได้ (path ย่อยก็ได้ ใช้ลิงก์แบบ relative หมด)
ต้องตั้ง cron บนเครื่องให้รัน `python scripts/fetch_news.py` ทุก 4 ชั่วโมง โดยมี `GEMINI_API_KEY` ใน environment

## ข้อควรรู้

- GitHub จะ **หยุด workflow ที่ตั้งเวลาไว้เอง ถ้า repo ไม่มีความเคลื่อนไหว 60 วัน** — ถ้าเว็บขึ้นแถบ "ข้อมูลไม่ได้อัปเดตนานแล้ว"
  ให้เข้าแท็บ Actions แล้วกดเปิด workflow ใหม่
- แหล่งข่าวบางแห่ง (เช่น Reddit) บางครั้งตอบ 429 — รอบนั้นข้ามไป รอบหน้าดึงใหม่ สถานะแต่ละแหล่งดูได้ที่แผงขวาของหน้าเว็บ
- สรุปโดย AI อาจคลาดเคลื่อน ทุกข่าวมีลิงก์ต้นฉบับ และแสดงหัวข้อภาษาอังกฤษเดิมไว้ใต้หัวข้อไทย

## โครงไฟล์

```
feeds.json                     แหล่งข่าว
scripts/fetch_news.py          ดึง → กรองซ้ำ → สรุปด้วย Gemini → เขียน JSON
site/index.html, style.css, app.js   หน้าเว็บ (ไม่มี build step)
site/data/news.json            ข้อมูลข่าว (Actions commit ให้เอง)
.github/workflows/update-news.yml    ตั้งเวลา + deploy GitHub Pages
```

## ต่างจาก design ทีม

- ไม่มี sidebar: เป็นหน้าเดียว เมนูเดียวจะเป็นเมนูที่ไม่มีที่ไป (หัวข้อ 5) จึงใช้ topbar + เนื้อหาแบบหน้า 11.1 แทน
- ใช้ HTML/CSS/JS ล้วนตามหัวข้อ 11.7 (ไม่มี Next.js) เพื่อให้วางบน GitHub Pages ได้โดยไม่ต้อง build
