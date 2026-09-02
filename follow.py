#!/usr/bin/env python3
"""
follow.py — ตามลิงก์สั้นด้วย urllib ล้วน (ฟรี ไม่เสีย Apify)

3 แบบที่ต้องรับมือ (ทั้งหมดอ่านเองได้ ไม่ต้องรันเบราว์เซอร์ ไม่ต้องรอเวลานับถอยหลัง):
  1. 301/302 ปกติ (shorturl.asia, bit.ly ทั่วไป) — urllib ตามให้เอง
  2. หน้า HTML สั้น ๆ ที่ฝังปลายทางใน <meta refresh> / location.replace() — t.co ใช้แบบนี้
  3. หน้า "คั่น" นับถอยหลัง 6-8 วิ (bit.ly ที่โดนตั้งธงว่าน่าสงสัย) — ปลายทางอยู่ใน JSON
     ของหน้า (`"Location"` / `"LongURL"`) อ่านตรง ๆ ได้เลย ไม่ต้องรอ
หน้า bio-link ที่ต้องคลิกเอง (heylink/linktr.ee) ใช้ targets() อ่านปุ่มทั้งหน้าแทน
"""

import html
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# header ชุดเต็มแบบเบราว์เซอร์จริง — bio-link อย่าง heylink ตอบ 403 ทันทีถ้าส่งแค่ User-Agent
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "th-TH,th;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1",
    "Cache-Control": "no-cache",
}

# โดเมนที่ไม่ใช่ปลายทางโฆษณา (โครงสร้างเว็บ/โซเชียล/บริการ QR) — กรองออกตอนอ่านหน้า bio-link
NOISE = (
    "google", "gstatic", "facebook", "fbcdn", "youtube", "youtu.be", "instagram", "tiktok",
    "twitter", "x.com", "telegram", "whatsapp", "cloudflare", "jsdelivr", "jquery", "stripe",
    "cookielaw", "w3.org", "schema.org", "gravatar", "vercel", "fonts.", "apple.com",
    "kode.link", "qrcode", "bootstrap", "cdn.", "unpkg", "sentry", "hotjar", "clarity.ms",
    "wixstatic", "parastorage", "wix.com", "googletagmanager", "doubleclick", "ajax.",
    "static.", "gtag", "analytics", "recaptcha", "bit.ly/js", "linktr.ee/s/",
)


def _ctx(verify=True):
    """ตัวตรวจใบรับรอง: ปกติตรวจตามปกติ (+ เติม root ของ Windows ให้ด้วย)

    เว็บย่อลิงก์บางเจ้า (bit.ly ที่ขึ้นหน้าเตือน) ส่งเชนใบรับรองไม่ครบ ทำให้ verify ล้ม
    เราแค่อ่าน URL ปลายทางของโฆษณาสาธารณะ ไม่ได้ส่งข้อมูลอะไรเข้าไป จึงยอมข้ามการตรวจ
    เป็นทางเลือกสุดท้ายได้ ดีกว่าปล่อยให้โพสต์นั้นค้างไม่รู้โดเมนตลอดไป"""
    if not verify:
        c = ssl.create_default_context()
        c.check_hostname = False
        c.verify_mode = ssl.CERT_NONE
        return c
    c = ssl.create_default_context()
    if hasattr(ssl, "enum_certificates"):          # Windows: ดึง root จาก cert store ของระบบ
        for cert, _enc, _trust in ssl.enum_certificates("ROOT"):
            try:
                c.load_verify_locations(cadata=cert)
            except Exception:
                pass
    return c


CTX, CTX_OFF = _ctx(True), _ctx(False)


def _openers(verify=True):
    """ลองยิงตรงก่อน แล้วค่อยหมุน proxy ที่ตั้งไว้ (bio-link ชอบบล็อก IP ที่ยิงถี่)"""
    from config import ENV
    ctx = CTX if verify else CTX_OFF
    out = [urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))]
    for px in [u.strip() for u in (ENV.get("proxy_url") or "").split(",") if u.strip()]:
        out.append(urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": px, "https": px}),
            urllib.request.HTTPSHandler(context=ctx)))
    return out


def page(url, timeout=25, max_bytes=1500000):
    """โหลด HTML ของหน้า -> (html, ทางที่ใช้) · โดน 403/429 จะหมุน proxy ให้เอง"""
    err = "?"
    for verify in (True, False):
        for i, op in enumerate(_openers(verify)):
            try:
                r = op.open(urllib.request.Request(url, headers=UA), timeout=timeout)
                via = "ตรง" if i == 0 else "proxy#%d" % i
                return r.read(max_bytes).decode("utf-8", "ignore"), via + ("" if verify else "/no-tls")
            except Exception as ex:
                err = str(ex)[:80]
        if "SSL" not in err and "CERTIFICATE" not in err:
            break            # ไม่ใช่ปัญหาใบรับรอง ลองแบบไม่ verify ไปก็เท่านั้น
    return None, err


def keep_fn(url, own_host=None):
    """คืนฟังก์ชันคัดลิงก์ "ที่เป็นปลายทางจริง" ของหน้านั้น (ตัดลิงก์ในบ้านตัวเอง/โครงสร้าง/โซเชียล)"""
    own = own_host or _host(url)
    ownbase = own.split(".")[0]

    def keep(u):
        h = _host(u)
        if not h or h == own or own in h:
            return False
        if h.split(".")[0] == ownbase:      # hopp.bio -> hopp.co ก็ของเจ้าเดิม
            return False
        return not any(s in h for s in NOISE)
    return keep


def external(url, pairs):
    """กรอง [(href, ข้อความปุ่ม), ...] ที่ได้จากที่อื่น (เช่นเบราว์เซอร์ Apify) ด้วยกติกาเดียวกัน"""
    keep = keep_fn(url)
    out, seen = [], set()
    for href, txt in pairs:
        if href and keep(href) and href not in seen:
            seen.add(href)
            out.append((href, (txt or "").strip()[:60]))
    return out


_A = re.compile(r"""<a\s[^>]*href=["'](https?://[^"']+)["'][^>]*>(.*?)</a>""", re.S | re.I)
_ANYURL = re.compile(r"""https?://[^\s"'<>\)]+""")


def targets(url, own_host=None):
    """อ่านหน้า bio-link แล้วคืนลิงก์ปลายทางที่คนโฆษณาวางไว้ (เรียงตามลำดับในหน้า)

    ปุ่มส่วนใหญ่เป็น <a href> ตรง ๆ · บางเจ้า (hopp.bio) เป็น JS app ลิงก์ฝังใน JSON
    เลยมี fallback กวาด URL ทั้งหน้าแทน · คืน [(url, ข้อความบนปุ่ม), ...]"""
    body, via = page(url)
    if not body:
        return [], via
    keep = keep_fn(url, own_host)
    out, seen = [], set()
    for href, txt in _A.findall(body):
        if keep(href) and href not in seen:
            seen.add(href)
            label = re.sub(r"<[^>]+>", " ", txt)
            label = html.unescape(re.sub(r"\s+", " ", label)).strip()[:60]
            out.append((href, label))
    if not out:                       # หน้าแบบ JS app — กวาดจาก body ตรง ๆ
        for u in _ANYURL.findall(body):
            u = u.rstrip('\\",;')
            if keep(u) and u not in seen:
                seen.add(u)
                out.append((u, ""))
    return out, via


def _host(u):
    try:
        return (urllib.parse.urlparse(u).hostname or "").lower().replace("www.", "")
    except Exception:
        return ""

_META = re.compile(rb"""<meta[^>]+http-equiv=["']?refresh["']?[^>]*content=["'][^"']*?url=([^"'>\s]+)""",
                   re.I)
_JSLOC = re.compile(rb"""location\.replace\(\s*["']([^"']+)["']""", re.I)
# หน้า "คั่น" ที่นับถอยหลังก่อนพาไป (bit.ly เวลาโดนตั้งธงว่าน่าสงสัย) — ปลายทางฝังอยู่ใน JSON
# ของหน้าอยู่แล้ว ไม่ต้องรอครบ 6-8 วิ อ่านเอาตรง ๆ ได้เลย
_INTER = re.compile(rb"""["'](?:Location|long_?url|LongURL|destination)["']\s*[:=]\s*["']([^"']+)["']""",
                    re.I)


def _next_hop(body):
    """แกะปลายทางจาก meta refresh / location.replace / JSON ของหน้าคั่น"""
    for rx in (_META, _JSLOC, _INTER):
        m = rx.search(body or b"")
        if m:
            u = m.group(1).decode("utf-8", "ignore").strip()
            u = u.replace(chr(92) + "/", "/").replace(chr(92) + "u0026", "&")
            if u.startswith("//"):
                u = "https:" + u
            elif not u.startswith("http") and "." in u.split("/")[0]:
                u = "https://" + u          # bit.ly เก็บ LongURL แบบไม่มี scheme
            if u.startswith("http"):
                return u
    return None


def _is_ssl(ex):
    return isinstance(ex, ssl.SSLError) or isinstance(getattr(ex, "reason", None), ssl.SSLError)


def _get(url, timeout, read_bytes):
    """ยิง GET หนึ่งครั้ง

    ปกติจบในคำขอเดียว · จะลองซ้ำก็ต่อเมื่อ
      - โดน 403/429 (เว็บย่อลิงก์กันบอท/จำกัดอัตรา) -> หมุนไปใช้ proxy IP อื่น
      - เชนใบรับรองเสีย -> ลองใหม่แบบไม่ verify
    รหัสอื่น (404/500) โยนออกทันที ไม่ต้องเปลืองเวลาไล่ opener"""
    req = urllib.request.Request(url, headers=UA, method="GET")
    blocked = None
    for verify in (True, False):
        ssl_bad = False
        for op in _openers(verify):
            try:
                with op.open(req, timeout=timeout) as r:
                    return r.geturl(), r.read(read_bytes)
            except urllib.error.HTTPError as ex:
                if ex.code not in (403, 429):
                    raise
                blocked = ex                       # ลอง IP ถัดไป
            except Exception as ex:
                if _is_ssl(ex):
                    ssl_bad = True
                    break
                if blocked is None:
                    raise                          # ต่อไม่ติดตั้งแต่ทางตรง = ปัญหาจริง
        if not ssl_bad:
            break
    if blocked:
        raise blocked
    raise urllib.error.URLError("ตามลิงก์ไม่สำเร็จ")


def one(url, max_hops=5, timeout=10, read_bytes=65536):
    """คืน (final_url, error) — เดินตามให้สุดเท่าที่ทำได้โดยไม่ต้องรันเบราว์เซอร์

    อ่าน 64KB เพราะหน้าคั่นแบบนับถอยหลัง (bit.ly) ซ่อนปลายทางไว้กลางไฟล์ ไม่ใช่หัวไฟล์"""
    cur, err = url, None
    for _ in range(max_hops):
        body = b""
        try:
            landed, body = _get(cur, timeout, read_bytes)
        except urllib.error.HTTPError as ex:
            landed = ex.geturl() or cur
            err = "HTTP %s" % ex.code            # 403/404 ก็ยังได้ URL ที่ถูกต้องมาแล้ว
        except Exception as ex:
            return (cur if cur != url else None), str(ex)[:120]
        nxt = _next_hop(body)
        if not nxt or nxt == landed:
            return landed, err
        cur = urllib.parse.urljoin(landed, nxt)
    return cur, err


def many(urls, workers=8, timeout=10):
    """ตามหลายลิงก์พร้อมกัน -> {url: (final_url, error)}"""
    urls = [u for u in dict.fromkeys(urls) if u]
    if not urls:
        return {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return dict(zip(urls, pool.map(lambda u: one(u, timeout=timeout), urls)))
