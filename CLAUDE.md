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
- **`LLM_WORKERS`** (ค่าเริ่ม 8) — `pipeline._extract_many` ยิง OpenRouter ขนานด้วย ThreadPoolExecutor
  เขียน DB ยังอยู่เธรดเดียวเหมือนเดิม · `extract.extract` retry 3 ครั้งเมื่อโดน 429/5xx (ถอย 2/4/6 วิ)
  ถ้าเจอ 429 รัว ๆ ให้ลดเลขนี้ลง
- **`follow.py` มาก่อน Apify เสมอ** — `pipeline._resolve_urls` ตามลิงก์ด้วย urllib ฟรีก่อน
  เหลือเฉพาะที่ยังเป็น `REDIRECTORS` ค่อยส่ง Apify · t.co ไม่ตอบ 301 ต้องแกะจาก `<meta refresh>`
  ในบอดี้ (อย่าเปลี่ยนไปใช้ HEAD อย่างเดียว มันคืน 200 เปล่า)
- **อ่านหน้า bio-link ต้องใช้ header ชุดเต็ม** — `follow.UA` มี `Sec-Fetch-*`/`Accept-Language` ครบ
  เพราะ heylink ตอบ 403 ทันทีถ้าส่งแค่ User-Agent · โดน 429 จะหมุน `PROXY_URL` ให้เอง
  · `page()` อ่านถึง 1.5MB เพราะ hopp.bio (Wix) ฝังลิงก์จริงไว้ลึกเกิน 400KB
- **bio-link ที่มี Cloudflare (heylink)** — urllib ผ่านไม่ได้แน่นอน (`Cf-Mitigated: challenge`)
  ต้องใช้ `apify.page_links()` เปิดด้วยเบราว์เซอร์จริง + **residential proxy เท่านั้น**
  (datacenter/webshare โดนตีตกตั้งแต่ชั้นแรก) · pageFunction ต้อง **รอจน `<a href>` โผล่**
  ไม่ใช่รอเวลาคงที่ ไม่งั้นได้หน้า "Just a moment..." ที่ไม่มีลิงก์เลยกลับมา
- **หน้าคั่นนับถอยหลัง ไม่ต้องรอ** — bit.ly ที่โดนตั้งธงจะขึ้นหน้า "redirect in 6 seconds"
  แต่ปลายทางอยู่ใน JSON ของหน้าแล้ว (`"Location"`/`"LongURL"`) · `follow.one` อ่าน 64KB
  เพราะค่านั้นอยู่กลางไฟล์ ไม่ใช่หัวไฟล์
- **SSL ของเว็บย่อลิงก์พัง** — bit.ly ส่งเชนใบรับรองไม่ครบ Python บน Windows verify ไม่ผ่าน
  `follow._get` จึงลอง verify ก่อน (เติม root ของ Windows ให้แล้ว) แล้วค่อยถอยเป็นไม่ verify
  ยอมได้เพราะแค่อ่าน URL ปลายทางของโฆษณาสาธารณะ ไม่ได้ส่งข้อมูลเข้าไป
- **AI คืน url_landing เป็นโดเมนเปล่า** (ไม่มี `https://`) — `pipeline._norm_url` เติม scheme ให้
  ไม่งั้น urlparse อ่านโฮสต์ไม่ออก และ Apify ตีกลับ 400 invalid-input
- **`target_url`/`target_brand` ไม่ทับ `final_url`** — `final_url` = หน้า bio-link ที่โฆษณาใช้จริง
  ส่วน target = โดเมนแบรนด์หลังทะลุ · UI โชว์ target ก่อนเสมอ (`brandOf`/`landingOf` ใน index.html)
- **คำจาก AI เข้าคลังแบบปิดไว้** (`add_keywords(..., active=0)`) — ถ้าเปิดอัตโนมัติ คลังจะบาน
  จากหลักสิบเป็นหลักพันในไม่กี่รอบ = ค่า Apify บานตาม
- **ค่าใช้จ่าย Apify**: ทั้ง scrape + resolve เสียเงินตาม usage — collector อย่าตั้งถี่ (`COLLECT_CYCLE`)
- **ความลับ**: `.env` + `data/` อยู่ใน `.gitignore` แล้ว — อย่า commit token/proxy/DB ขึ้น git

- **หน้าเว็บโหลดแบบเพิ่มทีละนิด** — `/api/posts?since=<epoch>` เทียบกับ `updated_at`
  (ไม่ใช่ `ingested_at` เพราะ resolve แก้แถวเดิม ไม่ได้เก็บใหม่) · โหลดเต็มก้อนเฉพาะตอนเปิดหน้า
  กดรีเฟรช ทุก 1 นาที และตอนงานเบื้องหลังจบ · ตารางวาดทีละ 150 แถว (`SHOW`)
  ถ้าเผลอกลับไปโหลดเต็มทุก 6 วิ หน้าเว็บจะกระตุกและรูปกระพริบทันที (payload ~3MB)
- **รันหลาย server ซ้อนพอร์ตเดียวได้บน Windows** (`allow_reuse_address`) — ตัวเก่าไม่ตายจริง
  แล้วตอบคำขอสลับกับตัวใหม่ ทำให้เหมือนโค้ดใหม่ไม่ทำงาน · เช็คด้วย `netstat -ano | findstr 8770`

## API ของ server (สำหรับต่อยอด UI)
- `GET /api/posts` `{rows}` · `GET /api/keywords` `{rows}` · `GET /api/stats` · `GET /api/status`
- `GET /api/run-status` สถานะ+log การดึงล่าสุด (หน้าเว็บ poll ทุก 2 วิ)
- `POST /api/run` `{term}` (เว้นว่าง=ทุกคำ) หรือ `{terms:[...]}` (เฉพาะคำที่เลือก)
- `POST /api/keyword` `{term[,active][,del]}`
- `POST /api/keywords/bulk` `{terms:[], action:"on"|"off"|"del"}` — หน้าคลังคำค้นหาใช้ (ยืนยันครั้งเดียวต่อชุด)
- `POST /api/resolve-pending` — กวาดโพสต์ที่ยังไม่รู้โดเมนจริง (ปุ่ม "ตามโดเมนที่ค้าง" / `run.py fix`)
- `POST /api/resolve-bio` — ทะลุหน้า bio-link หาโดเมนแบรนด์จริง (ปุ่ม "ทะลุ bio-link" / `run.py bio`)

## repo
GitLab: https://gitlab.com/tardev-group/x-pulldata (branch หลัก: main)
