#!/usr/bin/env python3
"""
follow.py — ตามลิงก์สั้นด้วย urllib ล้วน (ฟรี ไม่เสีย Apify)

t.co ไม่ตอบ 301 แต่ตอบหน้า HTML สั้น ๆ ที่ฝังปลายทางไว้ใน <meta refresh> + location.replace()
เลยต้องอ่าน body 4KB แรกแล้วแกะเอา · ส่วน bit.ly/shorturl.asia ตอบ 301 ปกติ urllib ตามให้เอง
ที่ตามต่อไม่ได้จริง ๆ (หน้า bio-link ที่ต้องคลิก เช่น heylink) ถือว่าจบแค่นั้น
"""

import html
import re
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


def _openers():
    """ลองยิงตรงก่อน แล้วค่อยหมุน proxy ที่ตั้งไว้ (bio-link ชอบบล็อก IP ที่ยิงถี่)"""
    from config import ENV
    out = [urllib.request.build_opener()]
    for px in [u.strip() for u in (ENV.get("proxy_url") or "").split(",") if u.strip()]:
        out.append(urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": px, "https": px})))
    return out


def page(url, timeout=25, max_bytes=1500000):
    """โหลด HTML ของหน้า -> (html, ทางที่ใช้) · โดน 403/429 จะหมุน proxy ให้เอง"""
    err = "?"
    for i, op in enumerate(_openers()):
        try:
            r = op.open(urllib.request.Request(url, headers=UA), timeout=timeout)
            return r.read(max_bytes).decode("utf-8", "ignore"), ("ตรง" if i == 0 else "proxy#%d" % i)
        except Exception as ex:
            err = str(ex)[:80]
    return None, err


_A = re.compile(r"""<a\s[^>]*href=["'](https?://[^"']+)["'][^>]*>(.*?)</a>""", re.S | re.I)
_ANYURL = re.compile(r"""https?://[^\s"'<>\)]+""")


def targets(url, own_host=None):
    """อ่านหน้า bio-link แล้วคืนลิงก์ปลายทางที่คนโฆษณาวางไว้ (เรียงตามลำดับในหน้า)

    ปุ่มส่วนใหญ่เป็น <a href> ตรง ๆ · บางเจ้า (hopp.bio) เป็น JS app ลิงก์ฝังใน JSON
    เลยมี fallback กวาด URL ทั้งหน้าแทน · คืน [(url, ข้อความบนปุ่ม), ...]"""
    body, via = page(url)
    if not body:
        return [], via
    own = own_host or _host(url)

    ownbase = own.split(".")[0]

    def keep(u):
        h = _host(u)
        if not h or h == own or own in h:
            return False
        if h.split(".")[0] == ownbase:      # hopp.bio -> hopp.co ก็ของเจ้าเดิม
            return False
        return not any(s in h for s in NOISE)

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


def _next_hop(body):
    """แกะปลายทางจาก meta refresh / location.replace ในหน้าเปลี่ยนทาง"""
    for rx in (_META, _JSLOC):
        m = rx.search(body or b"")
        if m:
            u = m.group(1).decode("utf-8", "ignore").replace(chr(92) + "/", "/").strip()
            if u.startswith("http"):
                return u
    return None


def one(url, max_hops=5, timeout=10, read_bytes=4096):
    """คืน (final_url, error) — เดินตามให้สุดเท่าที่ทำได้โดยไม่ต้องรันเบราว์เซอร์"""
    cur, err = url, None
    for _ in range(max_hops):
        body = b""
        try:
            req = urllib.request.Request(cur, headers=UA, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                landed = r.geturl()              # urllib ตาม 301/302 ให้แล้ว
                body = r.read(read_bytes)
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
