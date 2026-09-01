#!/usr/bin/env python3
"""
pipeline.py — ท่อหลัก: สแครป -> สกัด (AI) -> ตามลิงก์หาโดเมนจริง -> เก็บลง DB

แทนที่ flow ทั้งเส้นใน n8n. เรียกได้ 2 ทาง:
  - run_keyword("สล็อตเว็บตรง")  ทำทีละคีย์เวิร์ด
  - run_all()                    วนทุกคีย์เวิร์ดที่ active (collector ใช้ตัวนี้)

ลำดับต่อโพสต์: ข้ามถ้ามีใน DB แล้ว -> AI สกัด -> ถ้าไม่ใช่พนันก็บันทึกแบบ flag ไว้ (ไม่ resolve)
-> ถ้าเป็นพนันและมีลิงก์ landing ค่อยรวมลิงก์ไป resolve ทีเดียว -> อัปเดตโดเมนจริง
คีย์เวิร์ดใหม่ที่ AI สกัดได้ (marketing_keywords) เก็บเข้าคลังเป็น seed รอบถัดไป (วงจรโต)
"""

import re
import urllib.parse

import apify
import db
import extract
from config import ENV, NOT_GAMBLING
from fields import extract_fields, media

# ลิงก์สั้น/รวมลิงก์ (bio-link) ที่ต้องตามต่อจริง — apidojo คลี่ t.co ให้แล้ว แต่พวกนี้ยังเป็นด่านกลาง
SHORTENERS = {
    "t.co", "bit.ly", "tinyurl.com", "cutt.ly", "shorturl.asia", "shorturl.at", "rb.gy",
    "gg.gg", "s.id", "is.gd", "ow.ly", "lnkd.in", "reurl.cc",
    "heylink.me", "hopp.bio", "linktr.ee", "lit.link", "bio.link", "beacons.ai",
    "allmylinks.com", "msha.ke", "komi.io", "solo.to", "taplink.cc", "linkr.bio", "direc.to",
}


def _host(url):
    try:
        h = (urllib.parse.urlparse(url).hostname or "").lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def _brand_of(url):
    """joker388th.com -> joker388th (เหมือน pageFunction ของ resolver)"""
    h = _host(url)
    return h.split(".")[0] if h else None


def _needs_resolve(url):
    if ENV.get("resolve_all") == "1":
        return True
    h = _host(url)
    if not h:
        return False
    return h in SHORTENERS or any(h.endswith("." + s) for s in SHORTENERS)


def _best_landing(fld, ai):
    """เลือกลิงก์ที่ควรตามต่อ: url_landing ที่ AI ให้ก่อน แล้วค่อยลิงก์ดิบในแคปชั่น/bio"""
    for v in (ai.get("url_landing"), fld.get("cap_url"),
              ai.get("url_landing_2"), fld.get("ref_url"), fld.get("ref_url2")):
        if v and str(v).startswith("http"):
            return str(v)
    return None


def _norm_kw(term):
    term = re.sub(r"^#", "", (term or "").strip())
    return term if 1 < len(term) <= 80 else None


def run_keyword(term, log=print):
    """คืน dict สรุปผลของคีย์เวิร์ดนี้"""
    log("[scrape] %s" % term)
    tweets, err = apify.scrape(term)
    if err:
        db.log_run(term, 0, 0, 0, "scrape error: " + err)
        db.mark_keyword_run(term, 0)
        return {"term": term, "error": err}

    added = skipped = 0
    to_resolve = {}      # landing_url -> [tweet_id, ...] ที่ต้องอัปเดตด้วยผล resolve
    pending = {}         # tweet_id -> record ที่รอ resolve
    new_kw = set()

    for it in tweets:
        fld = extract_fields(it)
        tid = fld.get("tweet_id")
        if not tid or db.post_exists(tid):
            skipped += 1
            continue
        ai = extract.extract(fld)
        if "_error" in ai:
            log("  [ai] %s -> %s" % (tid, ai["_error"]))
            skipped += 1
            continue

        brand = ai.get("brand_name")
        is_gam = brand != NOT_GAMBLING
        img, video = media(it)
        rec = {
            "tweet_id": tid, "tweet_url": fld.get("tweet_url"),
            "created_at": fld.get("created_at"), "author": fld.get("author"),
            "location": fld.get("location"), "text": fld.get("text"),
            "brand_name": brand, "final_brand": None,
            "promotions": ai.get("promotions"), "keywords": ai.get("marketing_keywords"),
            "cta": ai.get("call_to_action"),
            "url_landing": ai.get("url_landing") or fld.get("cap_url"),
            "url_landing_2": ai.get("url_landing_2") or fld.get("ref_url2"),
            "final_url": None, "line_id": fld.get("line_id"),
            "preview_img": img, "video_url": video,
            "is_gambling": is_gam, "search_term": term, "raw": ai,
        }

        if is_gam:
            for kw in (ai.get("marketing_keywords") or []):
                n = _norm_kw(kw)
                if n:
                    new_kw.add(n)
            landing = _best_landing(fld, ai)
            if landing and _needs_resolve(landing):
                to_resolve.setdefault(landing, []).append(tid)
                pending[tid] = (rec, landing)
                continue          # เป็น shortener -> บันทึกทีหลังพร้อมโดเมนจริง
            if landing:
                # apidojo คลี่เป็นโดเมนจริงมาแล้ว ใช้ได้เลย ไม่ต้องเปลือง resolver
                rec["final_url"] = landing
                rec["final_brand"] = _brand_of(landing)

        # ไม่ใช่พนัน / ไม่มีลิงก์ / โดเมนจริงอยู่แล้ว -> บันทึกเลย
        if db.upsert_post(rec):
            added += 1

    # ---- resolve ลิงก์ทั้งหมดของคีย์เวิร์ดนี้ในรันเดียว ----
    if to_resolve:
        log("[resolve] %d ลิงก์" % len(to_resolve))
        resolved, rerr = apify.resolve(list(to_resolve.keys()))
        if rerr:
            log("  [resolve] error: %s (บันทึกแบบยังไม่รู้โดเมนจริง)" % rerr)
            resolved = {}
        for tid, (rec, landing) in pending.items():
            r = resolved.get(landing) or {}
            fu = r.get("finalUrl")
            if fu and fu not in ("ดึงไม่สำเร็จ", "ระบบขัดข้อง"):
                rec["final_url"] = fu
                rec["final_brand"] = r.get("brandName")
            if db.upsert_post(rec):
                added += 1

    if new_kw:
        got = db.add_keywords(sorted(new_kw), source="ai")
        if got:
            log("[keywords] +%d คำใหม่จาก AI" % got)

    db.log_run(term, len(tweets), added, skipped)
    db.mark_keyword_run(term, added)
    log("[done] %s: ดึง %d · บันทึกใหม่ %d · ข้าม %d" % (term, len(tweets), added, skipped))
    return {"term": term, "scraped": len(tweets), "added": added, "skipped": skipped}


def run_all(limit=None, log=print):
    kws = db.active_keywords()
    if limit:
        kws = kws[:int(limit)]        # collector หมุนทีละชุด ไม่ยิงทุกคำรวดเดียว
    out = []
    for k in kws:
        try:
            out.append(run_keyword(k["term"], log=log))
        except Exception as ex:
            log("[error] %s: %s" % (k["term"], ex))
    return out
