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
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import apify
import db
import extract
import follow
from config import ENV, NOT_GAMBLING
from fields import extract_fields, media

# ตัวเปลี่ยนทางล้วน — ตามด้วย urllib ได้ฟรี ไม่ต้องเปิดเบราว์เซอร์
REDIRECTORS = {
    "t.co", "bit.ly", "tinyurl.com", "cutt.ly", "shorturl.asia", "shorturl.at", "rb.gy",
    "gg.gg", "s.id", "is.gd", "ow.ly", "lnkd.in", "reurl.cc",
}
# หน้า bio-link (ต้องคลิกเอง ไม่มีปลายทางเดียว) — ถือว่านี่คือ landing จริงของโฆษณาแล้ว
BIOLINKS = {
    "heylink.me", "hopp.bio", "linktr.ee", "lit.link", "bio.link", "beacons.ai",
    "allmylinks.com", "msha.ke", "komi.io", "solo.to", "taplink.cc", "linkr.bio",
    "direc.to", "carrd.co",
}
SHORTENERS = REDIRECTORS | BIOLINKS


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


def _in(url, hosts):
    h = _host(url)
    return bool(h) and (h in hosts or any(h.endswith("." + s) for s in hosts))


def _is_short(url):
    return _in(url, SHORTENERS)


def _norm_url(u):
    """AI บางทีคืนโดเมนเปล่า ๆ ("play.class289th4.com") ไม่มี scheme — เติมให้ก่อนใช้งาน
    ไม่งั้น urlparse อ่านโฮสต์ไม่ออก และ Apify ก็ตีกลับเป็น 400 invalid-input"""
    u = str(u or "").strip()
    if not u or " " in u:
        return None
    if u.startswith("//"):
        return "https:" + u
    if not u.startswith("http"):
        head = u.split("/")[0]
        return "https://" + u if "." in head and len(head) > 3 else None
    return u


def _needs_resolve(url):
    if ENV.get("resolve_all") == "1":
        return True
    return _is_short(url)


def _best_landing(fld, ai):
    """เลือกลิงก์ที่ควรตามต่อ

    X แทนลิงก์ในแคปชั่นด้วย t.co เสมอ AI จึงมักตอบ t.co กลับมา ทั้งที่ API ส่ง "ลิงก์จริง"
    ที่คนโพสต์วางไว้มาให้แล้วใน entities (= cap_url) เลยต้องเอา cap_url ขึ้นก่อน
    แล้วค่อยไล่หาอันที่ไม่ใช่ลิงก์สั้น ก่อนจะยอมใช้ลิงก์สั้นเป็นทางเลือกสุดท้าย"""
    cands = [fld.get("cap_url"), ai.get("url_landing"), ai.get("url_landing_2"),
             fld.get("ref_url"), fld.get("ref_url2")]
    cands = [u for u in (_norm_url(v) for v in cands) if u]
    for v in cands:                       # โดเมนจริง = จบเลย ไม่ต้องตามต่อ
        if not _is_short(v):
            return v
    for v in cands:                       # เหลือแต่ลิงก์สั้น: t.co ไว้ท้ายสุด (มันแค่เปลือกของ X)
        if _host(v) != "t.co":
            return v
    return cands[0] if cands else None


def _norm_kw(term):
    term = re.sub(r"^#", "", (term or "").strip())
    return term if 1 < len(term) <= 80 else None


def _extract_many(flds, log=print):
    """ยิง LLM ทีละโพสต์คือคอขวด (รอ network ไม่ใช่ CPU) — เธรดพูลเลยช่วยได้ตรง ๆ
    คืนผลเรียงลำดับเดิมเสมอ เพื่อ zip กลับเข้าคู่กับทวีตได้"""
    if not flds:
        return []
    n = max(1, int(ENV.get("llm_workers") or 8))
    if len(flds) == 1 or n == 1:
        return [_safe_extract(f) for f in flds]
    log("[ai] สกัด %d โพสต์ (ขนาน %d เธรด)" % (len(flds), n))
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=n) as pool:
        out = list(pool.map(_safe_extract, flds))
    log("[ai] เสร็จใน %.0f วิ (เฉลี่ย %.1f วิ/โพสต์)" % (time.time() - t0, (time.time() - t0) / len(flds)))
    return out


def _safe_extract(f):
    try:
        return extract.extract(f)
    except Exception as ex:
        return {"_error": "extract ล้ม: %s" % ex}


def _resolve_urls(urls, log=print):
    """ตามลิงก์ให้ถึงโดเมนจริง — urllib ก่อน (ฟรี เร็วกว่าหลายสิบเท่า) เหลือเท่าไรค่อยจ่ายให้ Apify
    คืน {url เดิม: (final_url, brand)}"""
    # เก็บคู่ (ค่าที่บันทึกไว้ -> URL ที่ยิงได้จริง) เพราะบางแถวเป็นโดเมนเปล่าไม่มี scheme
    norm = {}
    for u in dict.fromkeys(urls):
        n = _norm_url(u)
        if n:
            norm[u] = n
    out = {}
    if not norm:
        return out
    t0 = time.time()
    got = follow.many(list(dict.fromkeys(norm.values())), workers=int(ENV.get("llm_workers") or 8))
    left = []
    for u, n in norm.items():
        fu = (got.get(n) or (None, None))[0]
        # ตามจนพ้นตัวเปลี่ยนทางแล้ว = ใช้ได้เลย (หน้า bio-link ถือเป็น landing จริงของโฆษณา)
        if fu and not _in(fu, REDIRECTORS):
            out[u] = (fu, _brand_of(fu))
        else:
            left.append(u)
    log("[resolve] urllib ตามได้ %d/%d ลิงก์ ใน %.1f วิ (ฟรี)" % (len(out), len(norm), time.time() - t0))
    if left:
        log("[resolve] ส่งต่อ Apify อีก %d ลิงก์ (มีค่าใช้จ่าย)" % len(left))
        res, err = apify.resolve([norm[u] for u in left])
        if err:
            log("  [resolve] Apify error: %s" % err)
            res = {}
        for u in left:
            r = res.get(norm[u]) or {}
            fu = r.get("finalUrl")
            if fu and fu not in ("ดึงไม่สำเร็จ", "ระบบขัดข้อง"):
                out[u] = (fu, r.get("brandName"))
    return out


def resolve_pending(limit=None, log=print):
    """กวาดโพสต์เก่าที่ยังไม่รู้โดเมนจริง (แท็บ 'รอหาโดเมน')

    รอบสแครปปกติจะไม่แตะโพสต์เหล่านี้อีกเลย เพราะข้ามด้วย tweet_id ไปแล้ว
    ถ้าไม่มีตัวกวาดนี้ ของที่ resolve พลาดรอบแรกจะค้างถาวร"""
    rows = db.pending_landing(limit)
    if not rows:
        log("[pending] ไม่มีโพสต์ค้าง")
        return {"pending": 0, "fixed": 0}
    log("[pending] โพสต์รอหาโดเมน %d แถว" % len(rows))
    resolved = _resolve_urls([r["url_landing"] for r in rows], log)
    fixed = 0
    for r in rows:
        fu, brand = resolved.get(r["url_landing"]) or (None, None)
        if fu and db.set_final(r["tweet_id"], fu, brand):
            fixed += 1
    log("[pending] เติมโดเมนจริงให้ %d แถว (เหลือค้าง %d)" % (fixed, len(rows) - fixed))
    return {"pending": len(rows), "fixed": fixed}


# คำบนปุ่มที่บอกว่าเป็นทางเข้าเว็บจริง (ไม่ใช่ไลน์/โซเชียล) — ใช้จัดลำดับผู้สมัคร
_CTA = ("สมัคร", "เข้าเล่น", "ทางเข้า", "เว็บตรง", "register", "signup", "sign up", "play", "join")


def _pick_target(cands, resolved):
    """เลือกปลายทางที่ดีที่สุดจากปุ่มในหน้า bio-link

    เกณฑ์: เป็นโดเมนจริง (ไม่ใช่ลิงก์สั้น/ไลน์) > ปุ่มที่ข้อความชวนสมัคร > ลำดับบนหน้า"""
    best = None
    for i, (href, label) in enumerate(cands):
        url = resolved.get(href, href)
        h = _host(url)
        if not h or _in(url, REDIRECTORS) or "lin.ee" in h or "line.me" in h:
            continue
        score = 100 - i                                   # อยู่บนสุดของหน้า = เด่นสุด
        if any(w in (label or "").lower() for w in _CTA):
            score += 50
        if best is None or score > best[0]:
            best = (score, url)
    return best[1] if best else None


def resolve_bio(limit=None, log=print, use_apify=True):
    """ทะลุหน้า bio-link (heylink/linktr.ee/hopp.bio) ไปหาโดเมนแบรนด์จริง

    หน้าพวกนี้เป็น "ป้ายรวมลิงก์" ไม่ใช่ตัวแบรนด์ — ชื่อ heylink/hopp เลยไม่มีความหมายในรายงาน
    อ่าน HTML เอง (ฟรี) แล้วตามลิงก์ในปุ่มต่ออีกชั้นด้วย follow"""
    pages = {u: ids for u, ids in db.bio_pages().items() if _in(u, BIOLINKS)}
    if not pages:
        log("[bio] ไม่มีหน้า bio-link ที่ต้องตามต่อ")
        return {"pages": 0, "fixed": 0}
    urls = list(pages.keys())[:int(limit)] if limit else list(pages.keys())
    log("[bio] ตามต่อจากหน้า bio-link %d หน้า (%d โพสต์)"
        % (len(urls), sum(len(pages[u]) for u in urls)))

    fixed = 0
    blocked = []                      # หน้าที่ urllib อ่านไม่ได้ (Cloudflare challenge ฯลฯ)
    for i, u in enumerate(urls):
        cands, via = follow.targets(u)
        if not cands:
            log("  [bio] %s -> urllib อ่านไม่ได้ (%s)" % (_host(u), via))
            blocked.append(u)
            continue
        # ปุ่มหลายอันยังเป็นลิงก์สั้นอีกชั้น -> ตามให้สุดก่อนค่อยเลือก
        # ใช้ _resolve_urls เพราะลิงก์สั้นบางเจ้า (shorturl.asia) ก็อยู่หลัง Cloudflare เหมือนกัน
        # urllib จะโดน 403 แล้วตกไปให้เบราว์เซอร์ของ Apify ทำต่อเอง
        short = [h for h, _ in cands if _in(h, REDIRECTORS)]
        resolved = {k: v[0] for k, v in _resolve_urls(short, log).items()} if short else {}
        fixed += _save_target(u, cands, resolved, pages, via, log)
        if i < len(urls) - 1:
            time.sleep(1.2)           # อย่ายิงรัว เดี๋ยวโดน Cloudflare ตั้งด่านเอา

    # หน้าที่โดนด่านกันบอท ต้องใช้เบราว์เซอร์จริง (Apify) — ยิงรวดเดียวทั้งชุดให้คุ้มค่ารัน
    if blocked and use_apify:
        log("[bio] เหลือ %d หน้าที่ต้องเปิดด้วยเบราว์เซอร์จริง (Apify มีค่าใช้จ่าย)" % len(blocked))
        got, err = apify.page_links(blocked)
        if err:
            log("  [bio] Apify error: %s" % err)
        for u in blocked:
            cands = follow.external(u, got.get(u) or [])
            if not cands:
                log("  [bio] %s -> เบราว์เซอร์ก็อ่านไม่ได้ ข้าม" % _host(u))
                continue
            short = [h for h, _ in cands if _in(h, REDIRECTORS)]
            resolved = {k: v[0] for k, v in _resolve_urls(short, log).items()} if short else {}
            fixed += _save_target(u, cands, resolved, pages, "apify", log)
    elif blocked:
        log("[bio] เหลือ %d หน้าที่โดนด่านกันบอท (ข้ามไว้ก่อน)" % len(blocked))

    log("[bio] ได้ปลายทางรวม %d โพสต์" % fixed)
    return {"pages": len(urls), "fixed": fixed}


def _save_target(u, cands, resolved, pages, via, log):
    """เลือกปลายทางจากปุ่มที่อ่านมาได้ แล้วบันทึกให้ทุกโพสต์ที่ใช้หน้า bio นี้"""
    target = _pick_target(cands, resolved)
    if not target:
        log("  [bio] %s -> มีแต่ลิงก์โซเชียล ข้าม" % _host(u))
        return 0
    brand = _brand_of(target)
    n = db.set_target(pages[u], target, brand)
    log("  [bio] %s -> %s (%d โพสต์) via %s" % (_host(u), brand, n, via))
    return n


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
    got_url = {}         # tweet_id -> (final_url, brand) ที่รู้แล้วในรอบนี้
    no_link = []         # (tweet_id, ทวีตแม่) ของโพสต์พนันที่ไม่มีลิงก์ในตัวเอง
    new_kw = set()

    # เฟส 1: คัดเฉพาะทวีตที่ยังไม่มีใน DB (ไม่แตะเน็ต จบเร็ว)
    fresh = []
    for it in tweets:
        fld = extract_fields(it)
        tid = fld.get("tweet_id")
        if not tid or db.post_exists(tid):
            skipped += 1
            continue
        fresh.append((it, fld))

    # เฟส 2: สกัดด้วย AI ขนานกัน แล้วค่อยวนบันทึกทีละตัว (เขียน DB ในเธรดเดียวเหมือนเดิม)
    for (it, fld), ai in zip(fresh, _extract_many([f for _, f in fresh], log)):
        tid = fld["tweet_id"]
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
                got_url[tid] = (landing, rec["final_brand"])
            elif fld.get("reply_to"):
                no_link.append((tid, str(fld["reply_to"])))

        # ไม่ใช่พนัน / ไม่มีลิงก์ / โดเมนจริงอยู่แล้ว -> บันทึกเลย
        if db.upsert_post(rec):
            added += 1

    # ---- resolve ลิงก์ทั้งหมดของคีย์เวิร์ดนี้ในรันเดียว ----
    if to_resolve:
        resolved = _resolve_urls(list(to_resolve.keys()), log)
        for tid, (rec, landing) in pending.items():
            fu, brand = resolved.get(landing) or (None, None)
            if fu:
                rec["final_url"] = fu
                rec["final_brand"] = brand
                got_url[tid] = (fu, brand)
            if db.upsert_post(rec):
                added += 1

    # สแปม iGaming ชอบวางลิงก์ไว้ทวีตแรกทวีตเดียว แล้วตอบต่อท้ายเป็นสายยาวโดยไม่ใส่ลิงก์อีก
    # ทวีตลูกจึงไม่มีอะไรให้ตาม -> ยืมโดเมนจากทวีตแม่ถ้าแม่อยู่ในชุดเดียวกัน (ฟรี ไม่ยิงเพิ่ม)
    if no_link:
        n = 0
        for tid, parent in no_link:
            got = got_url.get(parent)
            if got and db.set_final(tid, got[0], got[1]):
                n += 1
        if n:
            log("[thread] ยืมโดเมนจากทวีตแม่ให้ %d โพสต์ที่ไม่มีลิงก์" % n)

    if new_kw:
        # เข้าคลังแบบ "ปิดไว้" — เจ้าของเป็นคนเลือกเปิดเองในหน้าคลังคีย์เวิร์ด
        # (ถ้าเปิดอัตโนมัติ คลังจะบานจากหลักสิบเป็นหลักพันภายในไม่กี่รอบ = ค่า Apify บาน)
        got = db.add_keywords(sorted(new_kw), source="ai", active=0)
        if got:
            log("[keywords] +%d คำใหม่จาก AI (ปิดไว้ รอเลือกเปิดในคลัง)" % got)

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
