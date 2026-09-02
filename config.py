#!/usr/bin/env python3
"""
config.py — โหลดคีย์/ค่าตั้งต้นทั้งหมดของ x-pulldata ไว้ที่เดียว

x-pulldata = ย้าย pipeline "สแครป X -> สกัดด้วย AI -> ตามลิงก์หาโดเมนจริง -> เก็บ/โชว์"
จาก n8n มาเป็นโค้ดล้วน (Python stdlib + SQLite + หน้า HTML เดียว) แนวเดียวกับ tools/ct-live

คีย์ไม่ hardcode ลงโค้ด — อ่านจาก environment หรือไฟล์ .env ข้าง ๆ (.env อยู่ใน .gitignore)
"""

import os
from pathlib import Path

HERE = Path(__file__).parent
DB_PATH = HERE / "data" / "pulldata.db"

# --- ปลายทาง API ---
APIFY_BASE = "https://api.apify.com/v2"
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
PORT = 8770                      # ct-live ใช้ 8765 — เลี่ยงชนกัน


def load_env():
    """อ่านค่าตั้งจาก environment ก่อน แล้ว fallback ไฟล์ .env ข้าง ๆ (เหมือน ct-live)"""
    cfg = {
        "apify_token":     os.environ.get("APIFY_TOKEN", ""),
        "scraper_actor":   os.environ.get("APIFY_SCRAPER_ACTOR", "apidojo/twitter-scraper-lite"),
        "resolver_actor":  os.environ.get("APIFY_RESOLVER_ACTOR", "apify/playwright-scraper"),
        "or_key":          os.environ.get("OPENROUTER_API_KEY", ""),
        "model":           os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite"),
        "proxy_url":       os.environ.get("PROXY_URL", ""),      # residential proxy สำหรับ resolver
        "max_items":       os.environ.get("MAX_ITEMS", "100"),
        "resolve_all":     os.environ.get("RESOLVE_ALL", ""),   # 1 = resolve ทุกลิงก์ (default: เฉพาะ shortener)
        "collect":         os.environ.get("COLLECTOR", ""),
        "collect_cycle":   os.environ.get("COLLECT_CYCLE", ""),
        "retain_days":     os.environ.get("RETAIN_DAYS", "0"),   # 0 = ไม่ลบทิ้ง
        "llm_workers":     os.environ.get("LLM_WORKERS", "8"),   # ยิง LLM ขนานกี่เธรด
        "scrape_workers":  os.environ.get("SCRAPE_WORKERS", "5"),  # ทำกี่คีย์เวิร์ดพร้อมกัน
    }
    f = HERE / ".env"
    if f.exists():
        keymap = {
            "APIFY_TOKEN": "apify_token", "APIFY_SCRAPER_ACTOR": "scraper_actor",
            "APIFY_RESOLVER_ACTOR": "resolver_actor", "OPENROUTER_API_KEY": "or_key",
            "OPENROUTER_MODEL": "model", "PROXY_URL": "proxy_url", "MAX_ITEMS": "max_items",
            "RESOLVE_ALL": "resolve_all", "COLLECTOR": "collect",
            "COLLECT_CYCLE": "collect_cycle", "RETAIN_DAYS": "retain_days",
            "LLM_WORKERS": "llm_workers", "SCRAPE_WORKERS": "scrape_workers",
        }
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            slot = keymap.get(k)
            # env ชนะไฟล์ .env เฉพาะคีย์/โทเคนที่ลับ ที่เหลือให้ .env เขียนทับ default ได้
            if slot and (not cfg[slot] or slot not in ("apify_token", "or_key", "proxy_url")):
                cfg[slot] = v
    return cfg


ENV = load_env()


def actor_path(actor):
    """apidojo/twitter-scraper-lite -> apidojo~twitter-scraper-lite (รูปแบบ path ของ Apify API)"""
    return actor.strip().replace("/", "~")


# ============================================================ prompt สกัดข้อมูล (AI)
# system prompt นี้ยกมาจากที่ใช้ใน n8n ทุกตัวอักษร เพื่อให้ผลสกัดเหมือนเดิมเป๊ะ
SYSTEM_PROMPT = """คุณคือนักวิเคราะห์ข้อมูล (Data Analyst) ชั้นนำที่เชี่ยวชาญด้านการตลาดออนไลน์และ Social Listening โดยเฉพาะในอุตสาหกรรม iGaming และคาสิโนออนไลน์ของประเทศไทย

หน้าที่ของคุณคือ: อ่านข้อมูลโพสต์โซเชียลมีเดีย (ประกอบด้วย ข้อความแคปชั่น, URL ของแบรนด์, และ Location) และสกัดข้อมูลสำคัญทางการตลาดออกมาให้แม่นยำที่สุด โดยต้องละทิ้งข้อความขยะ อีโมจิที่ไม่จำเป็น และลิงก์ต่างๆ ออกไป

ข้อกำหนดบังคับ (STRICT RULES):
1. วิเคราะห์ข้อความและแยกข้อมูลออกเป็น 4 หัวข้อเท่านั้นตามโครงสร้างที่กำหนด
2. การหา "brand_name": ให้พิจารณาจากข้อความแคปชั่น หรือแกะจาก URL/Location หากมีปรากฏ (เช่น ถ้า URL คือ joker388th.com ให้ตอบ joker388th)
3. การหา "marketing_keywords": สกัดแฮชแท็กหรือคำกระตุ้นความสนใจ "ใหม่ๆ" ที่ไม่ซ้ำซาก เพื่อนำไปใช้เป็นคีย์เวิร์ดค้นหาในอนาคต (ตัดเครื่องหมาย # ออก)
4. การหา "url_landing": ให้พิจารณาจากข้อความแคปชั่น หรือแกะจาก URL/Location หากมีปรากฏ (เช่น ถ้า URL คือ https://t.co/2kuLT5hy2o ให้ตอบ https://t.co/2kuLT5hy2o หรือ พิจารณาจาก https ด้วย)
5. หากข้อมูลส่วนไหนไม่มีปรากฏ ให้คืนค่าเป็น null (ห้ามเดาหรือคิดขึ้นเองเด็ดขาด)
6. ผลลัพธ์ต้องอยู่ในรูปแบบ JSON เท่านั้น ห้ามมีข้อความทักทาย ห้ามมีคำอธิบายเพิ่มเติม ห้ามมี Markdown block (เช่น ```json) คร่อมผลลัพธ์ ให้ตอบมาแค่ตัว JSON ล้วนๆ เริ่มต้นด้วย { และจบด้วย }
7. ข้อควรระวังสูงสุด: ข้อมูล brand_name ต้องสกัดจากตัวอักษรที่ปรากฏในข้อความที่ส่งให้เท่านั้น ห้ามสะกดผิด ห้ามคิดคำขึ้นมาเอง ห้ามเดาจากบริบทเด็ดขาด หากไม่พบชื่อแบรนด์ที่ชัดเจนให้ตอบ null ทันที
8. กฎเหล็กคัดกรองเนื้อหาที่ไม่เกี่ยวข้องขั้นเด็ดขาด (CRITICAL IGAMING ONLY FILTER):
ก่อนที่คุณจะทำการสกัดข้อมูล ให้ประเมินบริบทและความตั้งใจ (Intent) ของโพสต์นั้นอย่างเข้มงวด หากเนื้อหาในโพสต์นั้น "ไม่ใช่" โฆษณาหรือโปรโมตเกี่ยวกับ คาสิโนออนไลน์, สล็อต, บาคาร่า, การพนัน หรือ iGaming ให้ทำการปฏิเสธทันที! ตัวอย่างเช่น:
- โพสต์ข่าวบันเทิง, วงการบันเทิง, ค่ายเพลง, เพลงใหม่, วงไอดอล (เช่น ข่าวต้อนรับสมาชิก/ผู้บริหารค่ายเพลง)
- โพสต์การเงินทั่วไป, สินเชื่อ, เงินกู้ด่วน, กู้เงินนอกระบบ, แหล่งเงินทุน
- โพสต์หางาน, รับคนแพ็คงาน, งานออนไลน์ได้เงินจริง
- โพสต์ข่าวการเมือง, ข่าวสังคม หรือโพสต์พูดคุยทั่วไปของยูสเซอร์
หากเจอเนื้อหาที่ไม่เกี่ยวกับ iGaming เหล่านี้ ห้ามทำการสกัดโปรโมชั่นหรือคีย์เวิร์ดเด็ดขาด ให้กำหนดค่าทุกฟิลด์ใน JSON เป็น null ทั้งหมด และให้ใส่ "brand_name": "ไม่ใช่เว็บพนัน" เพื่อส่งสัญญาณให้ระบบหลังบ้านรู้ทันที

โครงสร้าง JSON ที่ต้องการ:
{
  "brand_name": "ชื่อเว็บไซต์หรือแบรนด์. กฎการหา: 1. หาจากในข้อความแคปชั่นก่อน 2. หากไม่พบในข้อความ ให้แกะจาก URL ที่ส่งให้ (url_landing หรือ url_landing_2) โดยตัดคำว่า .com, .net, vip, http, www ออกให้เหลือแค่ชื่อแบรนด์หลัก (เช่น หาก URL คือ ufg8-vip.com ให้ตอบว่า ufg8 หรือหาก URL คือ joker388th.com ให้ตอบ joker388th). ถ้าหาไม่พบจริงๆ ให้ใส่ (หาไม่เจอ)",
  "promotions": ["รายการโปรโมชั่นที่พบ (เช่น ฝาก 100 รับ 150, คืนยอดเสีย 5%). ให้แยกเป็น Array ของ String ถ้าไม่พบให้ใส่ []"],
  "marketing_keywords": ["คีย์เวิร์ดหรือแฮชแท็กที่น่าสนใจ (เช่น สล็อตเว็บตรง, แตกง่าย, วอเลท). ให้แยกเป็น Array ของ String ถ้าไม่พบให้ใส่ []"],
  "call_to_action": "ประโยคกระตุ้นการตัดสินใจที่พบ (เช่น สมัครเลย, คลิกลิงก์, แอดไลน์). ถ้าไม่พบให้ใส่ null",
  "url_landing": "URL ที่เจอในข้อความแคปชั่น หรือจากบรรทัด 'URL อ้างอิง' ถ้าไม่พบให้ใส่ null",
  "url_landing_2": "URL ที่ดึงมาจากบรรทัด 'URL2 อ้างอิง' ตามข้อมูลที่ส่งไปให้ ถ้าไม่พบให้ใส่ null"
}"""

# ค่า brand_name ที่ AI ส่งกลับมาเมื่อโพสต์ "ไม่ใช่เว็บพนัน" (ใช้กรองออกจากตารางหลัก)
NOT_GAMBLING = "ไม่ใช่เว็บพนัน"
