#!/usr/bin/env python3
"""
fields.py — แกะฟิลด์ที่ต้องใช้ออกจากทวีตดิบของ Apify (apidojo/twitter-scraper-lite)

ผลจาก actor รูปร่างไม่คงที่ (บางทีอยู่ใต้ author บางทีใต้ user) เลยไล่ลอง path หลายทาง
ตามลำดับ fallback แบบเดียวกับ expression ใน n8n เดิม (เครื่องหมาย || ต่อกัน)
"""

import re


def deep(obj, path):
    """deep(obj, 'author.entities.url.urls.0.expanded_url') -> ค่า หรือ None ถ้าตกที่ใดที่หนึ่ง"""
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if part.isdigit():
            if isinstance(cur, (list, tuple)) and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def first(obj, *paths):
    """คืนค่าแรกที่ไม่ว่างจากลิสต์ path (เลียนแบบ a || b || c ใน n8n)"""
    for p in paths:
        v = deep(obj, p)
        if v not in (None, "", [], {}):
            return v
    return None


# ---- Line@ / LINE id จากแคปชั่น (AI schema ไม่มีให้ เลยหาเองด้วย regex เบา ๆ) ----
_LINE_RE = re.compile(
    r"(?:line|ไลน์|ไอดีไลน์|id\s*line)\s*[:：]?\s*@?([a-zA-Z0-9._-]{2,30})", re.I)
_ATID_RE = re.compile(r"(?<![\w])@([a-zA-Z0-9._-]{3,30})")


def line_id(text):
    if not text:
        return None
    m = _LINE_RE.search(text)
    if m:
        return "@" + m.group(1).lstrip("@")
    # เผื่อเขียน "@ufabetvip" ลอย ๆ ในบรรทัดที่พูดถึง line/ไลน์
    for line in text.splitlines():
        if re.search(r"line|ไลน์", line, re.I):
            m = _ATID_RE.search(line)
            if m:
                return "@" + m.group(1)
    return None


def media(item):
    """คืน (preview_img, video_url) — วิดีโอเลือก mp4 บิตเรตสูงสุด"""
    arr = (first(item, "extendedEntities.media", "entities.media", "media") or [])
    if isinstance(arr, dict):
        arr = [arr]
    img = video = None
    for m in arr if isinstance(arr, list) else []:
        if not isinstance(m, dict):
            # apidojo บางเวอร์ชันคืน media เป็นลิสต์ของ url สตริงตรง ๆ
            if isinstance(m, str) and not img:
                img = m
            continue
        if not img:
            img = m.get("media_url_https") or m.get("media_url") or m.get("thumbnail")
        variants = deep(m, "video_info.variants") or m.get("variants") or []
        best_bit = -1
        for v in variants if isinstance(variants, list) else []:
            if isinstance(v, dict) and str(v.get("content_type", "")).endswith("mp4"):
                b = v.get("bitrate") or 0
                if b >= best_bit:
                    best_bit, video = b, v.get("url")
    # เผื่อ field ระดับบนสุด
    img = img or first(item, "image", "mediaUrl", "previewImage")
    video = video or first(item, "video", "videoUrl")
    return img, video


def tweet_id(item):
    v = first(item, "id", "id_str", "tweetId", "conversationId")
    if v:
        return str(v)
    url = first(item, "url", "twitterUrl", "tweetUrl") or ""
    m = re.search(r"/status/(\d+)", str(url))
    return m.group(1) if m else None


def tweet_url(item):
    u = first(item, "url", "twitterUrl", "tweetUrl")
    if u:
        return str(u)
    author = first(item, "author.userName", "author.screen_name", "user.screen_name", "username")
    tid = tweet_id(item)
    if author and tid:
        return "https://x.com/%s/status/%s" % (author, tid)
    return None


def extract_fields(item):
    """สรุปฟิลด์ที่ pipeline ต้องใช้ (ตรงกับ expression ใน n8n เดิม)"""
    full_text = first(item, "fullText", "full_text", "text", "rawContent") or ""
    return {
        "tweet_id":  tweet_id(item),
        "tweet_url": tweet_url(item),
        "created_at": first(item, "createdAt", "created_at", "date"),
        "author":    first(item, "author.userName", "author.screen_name",
                            "user.screen_name", "username"),
        "location":  first(item, "location", "user.location", "author.location"),
        "text":      full_text,
        # URL อ้างอิง (จาก entities.url ของโปรไฟล์)
        "ref_url":   first(item, "entities.url.urls.0.display_url",
                           "user.entities.url.urls.0.display_url",
                           "author.entities.url.urls.0.display_url"),
        # URL2 อ้างอิง (จาก description ของโปรไฟล์)
        "ref_url2":  first(item, "user.entities.description.urls.0.expanded_url",
                           "author.entities.description.urls.0.expanded_url"),
        # URL แคปชั่น (ลิงก์ในตัวโพสต์ มักเป็น t.co)
        "cap_url":   first(item, "entities.urls.0.expanded_url", "entities.urls.0.url"),
        "line_id":   line_id(full_text),
    }
