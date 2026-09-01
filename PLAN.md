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

## รอทดสอบกับของจริง (ต้องมีคีย์ API)
- [ ] ยิง Apify scrape จริง 1 คีย์เวิร์ด ดูรูปร่างฟิลด์ว่าตรง `fields.py` (ปรับ path ถ้าคลาด)
- [ ] resolver actor: ยืนยันว่า `apify/playwright-scraper` รับ input ชุดนี้ (ถ้าใช้ actor อื่น แก้ `APIFY_RESOLVER_ACTOR`)
- [ ] ตรวจโมเดล OpenRouter (`OPENROUTER_MODEL`) ให้ตรงกับที่ใช้ใน n8n

## ไอเดียต่อยอด (ยังไม่ทำ)
- ปุ่ม toggle เปิด/ปิดคีย์เวิร์ดบน sidebar (API `/api/keyword` มีแล้ว รอผูกปุ่ม)
- export CSV/สรุปแบรนด์ยอดฮิต + กราฟจำนวนโพสต์ต่อวัน
- resolve แบบ 2 ชั้น: ลอง follow redirect เองก่อน (ฟรี) ค่อย fallback Apify (ลดค่าใช้จ่าย)
- แจ้งเตือน (LINE Notify/webhook) เมื่อเจอแบรนด์/โปรใหม่
