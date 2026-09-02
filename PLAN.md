# PLAN — x-pulldata

แผนโปรเจกต์ย้าย pipeline "สแครป X การตลาด iGaming" จาก n8n → โค้ด

## เป้าหมาย
1. เลิกพึ่ง n8n — ทำทุก node เป็นโค้ด Python ที่รัน/แก้/เก็บ git เองได้
2. ผลลัพธ์เหมือนเดิม: ตารางแบบ Google Sheet (TEXT, แบรนด์, URL landing หลายชั้น, Line@, วันที่,
   ลิงก์โพสต์, รูป, วิดีโอ) แต่โชว์บนหน้าเว็บของเราเอง เรียลไทม์ กรอง/ค้นได้
3. คงคุณภาพการสกัด: ใช้ system prompt เดิมกับ OpenRouter แบบไม่แก้ตัวอักษร
4. วงจรคีย์เวิร์ดโตเอง: `marketing_keywords` ที่ AI สกัด → เข้าคลังเป็นคีย์เวิร์ดรอบถัดไป

## การตัดสินใจ (เคาะกับผู้ใช้แล้ว)
| เรื่อง | เลือก | เหตุผล |
|--------|-------|--------|
| สแตก | Python stdlib + SQLite + HTML | เหมือน ct-live รันได้ทันที ไม่ต้องลง dependency |
| scrape + resolve | เรียก Apify actor จากโค้ด | actor เดิมจัดการ residential proxy + browser ให้แล้ว เสถียร |
| ปลายทาง | หน้าเว็บ + DB ของเราเอง | เลิกใช้ Google Sheet |

## สถาปัตยกรรม
- **server.py** (127.0.0.1:8770) เสิร์ฟ `index.html` + JSON API + (ออปชัน) collector วนเก็บเอง
- **pipeline.py** ต่อ 4 สเต็ป; per โพสต์: ข้ามถ้าซ้ำ → AI สกัด → ถ้าไม่ใช่พนัน flag ไว้ →
  ถ้าเป็นพนัน+มีลิงก์ รวมลิงก์ resolve ทีเดียว → เก็บ
- **db.py** 3 ตาราง: `posts` (คีย์=tweet_id กันซ้ำ), `keywords` (seed/ai + active), `runs` (log)
- หน้าเว็บ **poll** `/api/posts` ทุก 6 วิ, `/api/run-status` ทุก 2 วิ ให้เห็นผลไหลสด

## สถานะ: ✅ เสร็จรอบแรก (v0.1)
- [x] โครงไฟล์ + config/.env
- [x] Apify scrape (run-sync-get-dataset-items) — body ตรงกับ n8n
- [x] OpenRouter extract — system prompt + user content เดิม
- [x] Apify resolve — pageFunction เดิม + คืน `request.url` เพื่อ batch ได้ (ประหยัด)
- [x] แกะฟิลด์ทวีต (id/url/media/line@) + fallback หลาย path แบบ n8n
- [x] SQLite + วงจรคีย์เวิร์ด
- [x] หน้าเว็บตาราง 11 คอลัมน์ + sidebar กรอง + ปุ่มดึง + log สด
- [x] compile ผ่าน + smoke test DB/fields ผ่าน

## ทดสอบกับของจริงแล้ว ✅ (2026-09-02)
- [x] Apify scrape จริง — ฟิลด์ตรง `fields.py` ทุกตัว (id/url/media/cap_url/ref_url2); apidojo คลี่
      t.co เป็น `expanded_url` มาให้แล้ว · `author.location` มักว่าง (`''`) -> คืน None ถูกต้อง
- [x] resolver `apify/playwright-scraper` + **webshare proxy** — ตาม t.co ไปโดเมนจริงได้
      (t.co -> osiris168.com, chopper88.dev) map กลับด้วย `request.url` ถูก
- [x] โมเดล `openai/gpt-4.1-mini` — สกัด brand/keywords ได้ + วน +11 คีย์เวิร์ดใหม่เข้าคลัง
- [x] เพิ่ม optimize: resolve เฉพาะ shortener/bio-link (โดเมนจริงใช้เลย) — คุมด้วย `RESOLVE_ALL`
- [x] multi-proxy: `PROXY_URL` คั่น comma -> actor หมุนสลับ
- [x] ลบคีย์เวิร์ดเองได้: ปุ่ม × ใน sidebar (`del:1`) + `run.py del "คำ"`
- [x] เร่งความเร็ว: สกัด AI ขนาน 8 เธรด (`LLM_WORKERS`) + retry 429 — คอขวดเดิมคือยิง LLM ทีละโพสต์

## รอบที่ 2 — แดชบอร์ด + ตามลิงก์ฟรี (2026-09-02)
- [x] **`follow.py`** ตามลิงก์เองด้วย urllib: t.co ไม่ตอบ 301 แต่ฝังปลายทางใน `<meta refresh>` /
      `location.replace()` เลยอ่าน body 4KB แรกแล้วแกะ · เดินได้ 5 ชั้น · **8 ลิงก์/2 วิ ฟรี**
      (เทียบ Apify resolver 3 ลิงก์/158 วิ) — Apify เหลือเป็นตัวสำรองอย่างเดียว
- [x] `_best_landing` เชื่อ `entities.urls[].expanded_url` จาก API ก่อนคำตอบ AI
      (X แทนลิงก์ในแคปชั่นด้วย t.co เสมอ AI จึงตอบ t.co ทั้งที่ API ให้ลิงก์จริงมาแล้ว)
- [x] `resolve_pending()` + ปุ่ม/CLI `run.py fix` — กวาดโพสต์ที่ resolve พลาด (เดิมค้างถาวรเพราะโดน
      dedupe ข้าม) · กวาดรอบแรกได้ 139/148 แถว
- [x] ยืมโดเมนจากทวีตแม่ (`reply_to`) — สแปมวางลิงก์ไว้ทวีตแรกทวีตเดียวแล้วตอบต่อเป็นสายยาว
- [x] คลังคำค้นหา: เลือกหลายคำ → เปิด/ปิด/ลบ รวดเดียว (`/api/keywords/bulk`) · คำจาก AI เข้าคลัง
      แบบ `active=0` กันคลังบานจาก 21 → 200+ คำในไม่กี่รอบ
- [x] `created_ts` (epoch) + migration — เรียงตามวันที่โพสต์จริงได้ (createdAt เป็นสตริงเรียงไม่ได้)
- [x] `LLM_WORKERS` สกัด AI ขนาน 8 เธรด — 2-5 วิ/โพสต์ เหลือ 0.3 วิ/โพสต์
- [x] **ทะลุหน้า bio-link** (`follow.targets` + `pipeline.resolve_bio`): heylink/hopp.bio/carrd
      ฝังลิงก์ปุ่มไว้ใน HTML อยู่แล้ว อ่านเองได้ฟรี · ต้องส่ง header ชุดเต็มแบบเบราว์เซอร์ (heylink
      ตอบ 403 ถ้าส่งแค่ UA) + หมุน proxy เมื่อโดน 429 · hopp.bio เป็น Wix ลิงก์อยู่ลึกเกิน 400KB
      ผลจริง: ทะลุได้ 232 โพสต์ · ตารางแบรนด์เปลี่ยนจาก heylink/hopp เป็นแบรนด์จริง
      (play 102 · proded888 79 · chopper88 79 · tesla789s 71)
- [x] รับมือหน้าคั่นนับถอยหลังของ bit.ly (อ่าน `"Location"` จาก JSON ไม่ต้องรอ 6-8 วิ) +
      ถอยเป็นไม่ verify SSL เมื่อเชนใบรับรองเสีย + เติม scheme ให้โดเมนเปล่าที่ AI คืนมา
      ผลรวม: โพสต์พนัน 975 · รู้โดเมนจริง 948 (97%) · ค้างที่มีลิงก์เหลือ 0
- [x] แดชบอร์ดใหม่ (แนว Proboard): sidebar ดำหุบได้, การ์ด KPI, โดนัท+กราฟเส้น (SVG เขียนเอง),
      แบรนด์ยอดฮิต, ฟีดล่าสุด, Export CSV, ดูรูปใหญ่, responsive

## ไอเดียต่อยอด (ยังไม่ทำ)
- ปุ่ม toggle เปิด/ปิดคีย์เวิร์ด (ปิดชั่วคราวโดยไม่ลบทิ้ง — API `/api/keyword` `{active}` มีแล้ว รอผูกปุ่ม)
- export CSV/สรุปแบรนด์ยอดฮิต + กราฟจำนวนโพสต์ต่อวัน
- resolve ชั้นแรกลอง follow redirect เองในโค้ด (ฟรี) ค่อย fallback Apify (ลดค่าใช้จ่ายอีก)
- แจ้งเตือน (LINE Notify/webhook) เมื่อเจอแบรนด์/โปรใหม่
