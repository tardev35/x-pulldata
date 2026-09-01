#!/usr/bin/env python3
"""
extract.py — ส่งแคปชั่น + URL เข้าโมเดล (ผ่าน OpenRouter) แล้วได้ JSON การตลาดกลับมา

user content สร้างในรูปแบบเดียวกับที่ป้อนใน n8n เป๊ะ เพื่อให้โมเดลตอบเหมือนเดิม
คืน dict ที่ parse แล้ว หรือ {"_error": ...} ถ้าเรียก/อ่านไม่ได้
"""

import json
import re
import urllib.error
import urllib.request

from config import ENV, OR_URL, PORT, SYSTEM_PROMPT


def _user_content(f):
    """f = ผลจาก fields.extract_fields()"""
    return (
        "โปรดสกัดข้อมูลจากโพสต์ต่อไปนี้:\n"
        "แคปชั่น: %s\n"
        "Location: %s\n"
        "URL อ้างอิง: %s\n"
        "URL2 อ้างอิง : %s\n\n"
        "URL แคปชั่น: %s\n"
    ) % (f.get("text") or "null", f.get("location") or "null",
         f.get("ref_url") or "null", f.get("ref_url2") or "null",
         f.get("cap_url") or "null")


def extract(f):
    if not ENV["or_key"]:
        return {"_error": "ยังไม่ได้ตั้ง OPENROUTER_API_KEY (ดู .env)"}
    body = json.dumps({
        "model": ENV["model"],
        "temperature": 0,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": _user_content(f)}],
    }).encode()
    req = urllib.request.Request(OR_URL, data=body, headers={
        "Authorization": "Bearer " + ENV["or_key"],
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:%d" % PORT,
        "X-Title": "x-pulldata",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as ex:
        return {"_error": "OpenRouter %s: %s" % (ex.code, ex.read()[:180].decode("utf-8", "ignore"))}
    except Exception as ex:
        return {"_error": "เรียก OpenRouter ไม่ได้: %s" % ex}
    txt = out.get("choices", [{}])[0].get("message", {}).get("content", "")
    m = re.search(r"\{.*\}", txt, re.S)          # กันโมเดลห่อด้วย ```json แม้สั่งห้ามแล้ว
    if not m:
        return {"_error": "โมเดลตอบไม่ใช่ JSON", "_raw": txt[:200]}
    try:
        data = json.loads(m.group(0))
    except Exception:
        return {"_error": "อ่าน JSON จากโมเดลไม่ได้", "_raw": txt[:200]}
    # ปรับชนิดให้ชัวร์ (บางที null กลายเป็นสตริง "null")
    def _arr(v):
        return v if isinstance(v, list) else ([] if v in (None, "", "null") else [str(v)])
    data["promotions"] = _arr(data.get("promotions"))
    data["marketing_keywords"] = _arr(data.get("marketing_keywords"))
    for k in ("brand_name", "call_to_action", "url_landing", "url_landing_2"):
        if data.get(k) in ("", "null"):
            data[k] = None
    return data
