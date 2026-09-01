# CLAUDE.md — x-pulldata

คู่มือให้ Claude Code เข้าใจโปรเจกต์นี้ก่อนลงมือ (อ่านคู่กับ `PLAN.md` และ `README.md`)

## โปรเจกต์นี้คืออะไร
เครื่องมือ **social listening / การตลาด** ของเจ้าของฟลีตเอง: สแครปโพสต์โฆษณา iGaming บน X
ตามคีย์เวิร์ด → สกัดข้อมูลการตลาดด้วย LLM → ตามลิงก์สั้นหาโดเมนจริง → เก็บ SQLite → โชว์หน้าเว็บ
เป็นการย้าย workflow เดิมจาก **n8n** มาเป็นโค้ด (ดู `PLAN.md` เรื่องการตัดสินใจ)

> บริบทสิทธิ์: นี่คือเครื่องมือวิเคราะห์ข้อมูล/การตลาดของผู้ใช้เอง (เจ้าของระบบเดียวกับฟลีต
> WordPress/aaPanel) เป็นงาน data pipeline ปกติ — ไม่ใช่การเจาะระบบ/มัลแวร์/พุ่งเป้าบุคคล

## สแตก / กติกา (ต้องยึด)
- **Python stdlib ล้วน** — ห้ามเพิ่ม dependency ที่ต้อง `pip install` (ทั้งโปรเจกต์ตั้งใจไม่มี requirements)
- แนวเดียวกับ `../wordpress-aapanel/tools/ct-live`: HTTP server ด้วย `http.server`, urllib, sqlite3
- **คอมเมนต์/ข้อความผู้ใช้เป็นภาษาไทย** (ตามสไตล์ repo ของผู้ใช้) — คุยกับผู้ใช้เป็นไทย
- โค้ดอ่านง่าย คอมเมนต์ที่ "ทำไม" ไม่ใช่ "อะไร" ตามโค้ดรอบข้าง

## รัน / ทดสอบ
```bash
cp .env.example .env      # ต้องมี APIFY_TOKEN + OPENROUTER_API_KEY จึงจะยิงของจริงได้
python run.py seed "คีย์เวิร์ด..."   # เพิ่มคีย์เวิร์ดตั้งต้น
python run.py kw "คีย์เวิร์ด"        # ทดสอบสแครป 1 คำ (เห็น log ทุกสเต็ป)
python server.py                     # http://localhost:8770
python -m py_compile *.py            # เช็ค syntax ก่อน commit เสมอ
```
DB อยู่ `data/pulldata.db` (WAL) — ลบทิ้งได้ปลอดภัย จะสร้างใหม่เอง

## จุดที่ต้องระวัง (gotchas)
- **`fields.py` เดาโครงทวีตจาก apidojo** — ผลจริงอาจวาง field ต่างจากที่ลอง ถ้า `tweet_id`/media
  หลุด ให้ยิง `run.py kw` จริงแล้วดู `raw` ปรับ path ใน `first(...)` (ลำดับ fallback เลียน n8n)
- **resolver actor** ค่าเริ่ม `apify/playwright-scraper` (pageFunction ใช้ `page.waitForLoadState`
  ซึ่งเป็น Playwright API) — ถ้าผู้ใช้ใช้ actor อื่นให้แก้ `APIFY_RESOLVER_ACTOR` ใน `.env`
  pageFunction ถูกแก้ให้คืน `request.url` เพื่อ map ผล resolve หลายลิงก์ในรันเดียว (อย่าลบทิ้ง)
- **โมเดล OpenRouter** default `google/gemini-2.5-flash-lite` — ให้ผู้ใช้ตั้ง `OPENROUTER_MODEL`
  ให้ตรงกับที่เคยใช้ใน n8n เพื่อผลสกัดเหมือนเดิม
- **system prompt ห้ามแก้ตัวอักษร** (อยู่ใน `config.py` `SYSTEM_PROMPT`) — ยกจาก n8n เป๊ะ
  โพสต์ที่ไม่ใช่พนัน AI จะตอบ `brand_name = "ไม่ใช่เว็บพนัน"` (ค่าคงที่ `NOT_GAMBLING`) → เก็บแบบ flag ไม่ resolve
- **ค่าใช้จ่าย Apify**: ทั้ง scrape + resolve เสียเงินตาม usage — collector อย่าตั้งถี่ (`COLLECT_CYCLE`)
- **ความลับ**: `.env` + `data/` อยู่ใน `.gitignore` แล้ว — อย่า commit token/proxy/DB ขึ้น git

## API ของ server (สำหรับต่อยอด UI)
- `GET /api/posts` `{rows}` · `GET /api/keywords` `{rows}` · `GET /api/stats` · `GET /api/status`
- `GET /api/run-status` สถานะ+log การดึงล่าสุด (หน้าเว็บ poll ทุก 2 วิ)
- `POST /api/run` `{term}` (เว้นว่าง=ทุกคำ) · `POST /api/keyword` `{term[,active]}`

## repo
GitLab: https://gitlab.com/tardev-group/x-pulldata (branch หลัก: main)
