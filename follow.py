#!/usr/bin/env python3
"""
follow.py — ตามลิงก์สั้นด้วย urllib ล้วน (ฟรี ไม่เสีย Apify)

t.co ไม่ตอบ 301 แต่ตอบหน้า HTML สั้น ๆ ที่ฝังปลายทางไว้ใน <meta refresh> + location.replace()
เลยต้องอ่าน body 4KB แรกแล้วแกะเอา · ส่วน bit.ly/shorturl.asia ตอบ 301 ปกติ urllib ตามให้เอง
ที่ตามต่อไม่ได้จริง ๆ (หน้า bio-link ที่ต้องคลิก เช่น heylink) ถือว่าจบแค่นั้น
"""

import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
      "Accept-Language": "th,en;q=0.8"}

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
