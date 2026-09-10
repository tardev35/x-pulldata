#!/usr/bin/env python3
"""
apify.py — เรียก Apify actor จากโค้ด (แทนที่ node ใน n8n)

สองงาน:
  1) scrape(term)      -> รัน twitter-scraper-lite แบบ sync ได้ทวีตกลับมาเป็นลิสต์เลย
  2) resolve(urls)     -> รัน playwright-scraper ตามลิงก์สั้น (t.co/bit.ly/tinyurl) ไปโดเมนจริง

ใช้ urllib ล้วน (ไม่มี dependency) · endpoint run-sync-get-dataset-items คืน dataset ตรง ๆ
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from config import APIFY_BASE, ENV, actor_path

UA = {"User-Agent": "x-pulldata/1.0"}


def _run_sync(actor, payload, timeout=300):
    """POST run-sync-get-dataset-items -> คืน (items, error)"""
    token = ENV["apify_token"]
    if not token:
        return [], "ยังไม่ได้ตั้ง APIFY_TOKEN (ดู .env)"
    url = "%s/acts/%s/run-sync-get-dataset-items?token=%s" % (
        APIFY_BASE, actor_path(actor), urllib.parse.quote(token, safe=""))
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={**UA, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as ex:
        raw = ex.read()[:800].decode("utf-8", "ignore")
        try:                                   # Apify ห่อ error เป็น JSON — เอาแค่ข้อความให้อ่านรู้เรื่อง
            msg = json.loads(raw).get("error", {}).get("message") or raw[:200]
        except Exception:
            msg = raw[:200]
        return [], "Apify %s: %s" % (ex.code, msg)
    except Exception as ex:
        return [], "เรียก Apify ไม่ได้: %s" % ex
    return (data if isinstance(data, list) else []), None


# ---------------------------------------------------------------- 1) scrape
def scrape(term, max_items=None):
    """ค้น X ตามคีย์เวิร์ด -> ลิสต์ทวีตดิบ (เหมือน body ที่ตั้งไว้ใน n8n)"""
    n = int(max_items or ENV.get("max_items") or 100)
    payload = {
        "searchTerms": [term],
        "includeSearchTerms": False,
        "maxItems": n,
        "sort": "Latest",
        "tweetLanguage": "th",
    }
    return _run_sync(ENV["scraper_actor"], payload, timeout=300)


# ------------------------------------------------------- 3) อ่านลิงก์ในหน้าด้วยเบราว์เซอร์จริง
# ใช้เมื่อ urllib อ่านหน้าไม่ได้ (heylink อยู่หลัง Cloudflare challenge — ต้องรัน JS ถึงจะผ่าน)
_LINKS_FN = r"""async ({ page, request }) => {
  try {
    await page.waitForLoadState('domcontentloaded').catch(() => {});
    // Cloudflare ขึ้นหน้า "Just a moment..." ก่อน (ไม่มี <a> เลย) เบราว์เซอร์จริงจะผ่านเองใน ~5 วิ
    // จึงต้องรอจน <a> โผล่ ไม่ใช่รอเวลาคงที่ ไม่งั้นได้หน้าเปล่ากลับมา
    try { await page.waitForSelector('a[href]', { timeout: 25000 }); } catch (e) {}
    await page.waitForTimeout(1500);
    const links = await page.$$eval('a[href]', els => els.slice(0, 120).map(a => ({
      href: a.href, text: (a.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 60)
    })));
    return { requestUrl: request.url, title: await page.title().catch(() => ''), links };
  } catch (e) {
    return { requestUrl: request.url, links: [], error: String(e).slice(0, 150) };
  }
}"""


def page_links(urls, timeout=300):
    """เปิดหน้าเว็บด้วยเบราว์เซอร์จริงแล้วคืนลิงก์ทั้งหมดในหน้า -> {url: [(href, text), ...]}"""
    urls = [u for u in dict.fromkeys(urls) if u]
    if not urls:
        return {}, None
    payload = {
        "startUrls": [{"url": u} for u in urls],
        "ignoreSslErrors": True,
        # ต้อง residential เท่านั้น — Cloudflare ตีตก datacenter/webshare ตั้งแต่ชั้นแรก
        # (คิดตาม GB ที่ใช้ ดูใน .env ว่าตั้ง PROXY_URL ไว้ก็ไม่ช่วยชั้นนี้)
        "proxyConfiguration": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
        "pageFunction": _LINKS_FN,
        "pageLoadTimeoutSecs": 60,
        "navigationTimeoutSecs": 60,
        "maxRequestRetries": 1,
        "maxConcurrency": 3,
    }
    items, err = _run_sync(ENV["resolver_actor"], payload, timeout=timeout)
    if err:
        return {}, err
    out = {}
    for it in items:
        if isinstance(it, dict) and it.get("requestUrl"):
            out[it["requestUrl"]] = [(l.get("href"), l.get("text"))
                                     for l in (it.get("links") or []) if isinstance(l, dict)]
    return out, None


# ---------------------------------------------------------------- 2) resolve
# pageFunction ยกจาก n8n เดิม แต่เพิ่มการคืน request.url เพื่อให้ resolve หลายลิงก์ในรันเดียวได้
# (แล้วค่อย map ผลกลับเข้าโพสต์ด้วย requestUrl) — ประหยัดกว่ายิง actor ทีละลิงก์มาก
_PAGE_FN = """async ({ page, request }) => {
  try {
    await page.waitForLoadState('domcontentloaded').catch(() => {});
    const currentUrl = page.url();
    if (!currentUrl || currentUrl.includes('google.com') || currentUrl.includes('shorturl.asia') || currentUrl.includes('tinyurl.com')) {
      return { requestUrl: request.url, finalUrl: 'ดึงไม่สำเร็จ', brandName: '(หาไม่เจอ)' };
    }
    let brand = '';
    try { brand = currentUrl.split('/')[2].replace('www.', '').split('.')[0]; }
    catch (e) { brand = '(หาไม่เจอ)'; }
    return { requestUrl: request.url, finalUrl: currentUrl, brandName: brand };
  } catch (e) {
    return { requestUrl: request.url, finalUrl: 'ระบบขัดข้อง', brandName: '(หาไม่เจอ)' };
  }
}"""

_PRE_NAV = """[
  async ({ page, request }, gotoOptions) => {
    gotoOptions.waitUntil = 'domcontentloaded';
    gotoOptions.timeout = 30000;
  }
]"""


def resolve(urls, timeout=300):
    """ตามลิงก์หลายอันในรันเดียว -> dict {requestUrl: {finalUrl, brandName}}"""
    urls = [u for u in dict.fromkeys(urls) if u]      # unique รักษาลำดับ
    if not urls:
        return {}, None
    proxy = {"useApifyProxy": True}
    if ENV.get("proxy_url"):
        # PROXY_URL รับได้หลายตัวคั่นด้วย comma -> actor หมุนสลับให้ (เช่น webshare TH หลาย session)
        plist = [u.strip() for u in ENV["proxy_url"].split(",") if u.strip()]
        if plist:
            proxy = {"proxyUrls": plist}
    payload = {
        "startUrls": [{"url": u} for u in urls],
        "ignoreSslErrors": True,
        "proxyConfiguration": proxy,
        "pageFunction": _PAGE_FN,
        "preNavigationHooks": _PRE_NAV,
        "pageLoadTimeoutSecs": 30,
        "navigationTimeoutSecs": 30,
        "maxRequestRetries": 2,
        "maxConcurrency": 5,
    }
    items, err = _run_sync(ENV["resolver_actor"], payload, timeout=timeout)
    if err:
        return {}, err
    out = {}
    for it in items:
        if isinstance(it, dict) and it.get("requestUrl"):
            out[it["requestUrl"]] = {"finalUrl": it.get("finalUrl"),
                                     "brandName": it.get("brandName")}
    return out, None
