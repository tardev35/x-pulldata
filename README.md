# x-pulldata

สแครปโพสต์การตลาด **iGaming บน X (Twitter)** ตามคีย์เวิร์ด → สกัดข้อมูลด้วย AI →
ตามลิงก์สั้นไปหาโดเมนจริง → เก็บลง SQLite → ดูบนหน้าเว็บแบบเรียลไทม์

ย้ายมาจาก workflow ใน **n8n** ให้เป็นโค้ดล้วน — เร็วกว่า ถูกกว่า แก้ง่าย เก็บใน git ได้
ใช้ **Python stdlib อย่างเดียว ไม่ต้อง `pip install`** (แนวเดียวกับ `tools/ct-live`)

---

## ท่อข้อมูล (แทน n8n ทั้งเส้น)

```
คีย์เวิร์ด ──▶ 1) Apify: twitter-scraper-lite ──▶ ทวีตดิบ
                                                    │
              2) OpenRouter (LLM) สกัด ◀────────────┘
                 brand / promo / keywords / cta / landing
                                                    │
              3) Apify: playwright-scraper ◀────────┘
                 ตาม t.co / bit.ly / tinyurl → โดเมนจริง + แบรนด์
                                                    │
              4) SQLite + หน้าเว็บ ◀────────────────┘
                 marketing_keywords ──▶ วนกลับเป็นคีย์เวิร์ดรอบถัดไป (วงจรโต)
```

## ติดตั้ง / รัน

```bash
cd C:\x-pulldata
cp .env.example .env          # แล้วเติม APIFY_TOKEN + OPENROUTER_API_KEY (+ PROXY_URL ถ้ามี)

python run.py seed "สล็อตเว็บตรง" "เครดิตฟรี" "บาคาร่าออนไลน์"   # ใส่คีย์เวิร์ดตั้งต้น
python server.py                                                  # เปิด http://localhost:8770
```

บนหน้าเว็บกดปุ่ม **▶ ดึงจาก X** (พิมพ์คีย์เวิร์ด หรือเว้นว่าง = ทุกคำ) แล้วดูผลไหลเข้าตารางสด

### รันจาก command line (ไม่ต้องเปิดเว็บ)

```bash
python run.py kw "สล็อตเว็บตรง"   # สแครปคีย์เวิร์ดเดียว
python run.py all                 # ทุกคีย์เวิร์ดที่ active
python run.py list                # ดูคลังคีย์เวิร์ด
python run.py stats               # สรุปตัวเลข
```

### ให้เก็บเองอัตโนมัติ
ตั้ง `COLLECTOR=1` ใน `.env` แล้ว server จะวนสแครปคีย์เวิร์ดที่ active เองทีละ 5 คำทุก ๆ
`COLLECT_CYCLE` วินาที (ค่าเริ่ม 900 = 15 นาที)

## หน้าจอ (ตามคอลัมน์ Google Sheet เดิม)

`# · TEXT · ชื่อแบรนด์ · URL Landing · URL On post · URL Landing 2 · Line@ · DATE · POST X · Preview · VDO`

เมนูซ้าย: กรองตามมุมมอง (ทั้งหมด/เว็บพนัน/ไม่ใช่พนัน/มีวิดีโอ/รอหาโดเมน), กรองตามคีย์เวิร์ด
(นับสด), ช่องค้นหา และกล่องสั่งดึงข้อมูลพร้อม log สด

## ไฟล์

| ไฟล์ | หน้าที่ |
|------|---------|
| `config.py`   | โหลด `.env` + system prompt สกัดข้อมูล (ยกจาก n8n) |
| `apify.py`    | เรียก Apify actor: `scrape()` + `resolve()` |
| `extract.py`  | เรียก OpenRouter สกัดเป็น JSON |
| `fields.py`   | แกะฟิลด์จากทวีตดิบ (id/url/media/line@ …) |
| `pipeline.py` | ท่อหลัก scrape→extract→resolve→store + วงจรคีย์เวิร์ด |
| `db.py`       | SQLite (posts / keywords / runs) |
| `server.py`   | เว็บเซิร์ฟเวอร์ + API + collector |
| `index.html`  | หน้าเว็บตาราง |
| `run.py`      | CLI |

## หมายเหตุ / ค่าใช้จ่าย
- ทั้ง scrape และ resolve เรียก **Apify actor** (มีค่าใช้จ่ายตาม usage) — อย่าตั้ง `COLLECT_CYCLE` ถี่เกิน
- `resolve()` รวมทุกลิงก์ของคีย์เวิร์ดยิง actor **รันเดียว** (map กลับด้วย `request.url`) ประหยัดกว่าเดิมมาก
- `.env`, `data/` อยู่ใน `.gitignore` — ความลับกับฐานข้อมูลไม่ขึ้น git
