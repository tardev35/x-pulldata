#!/usr/bin/env python3
"""
db.py — SQLite ของ x-pulldata: เก็บโพสต์ที่สกัดแล้ว + คลังคีย์เวิร์ด + log การรัน

ทุกฟังก์ชันเปิด/ปิด connection เอง กันปัญหา sqlite ข้ามเธรด (server เป็น ThreadingHTTPServer)
เขียนผ่าน _lock ตัวเดียว กัน "database is locked" ตอน collector เขียนพร้อม browser อ่าน
"""

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone

from config import DB_PATH

_lock = threading.Lock()


def _con():
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")       # อ่าน/เขียนพร้อมกันได้ ไม่ล็อกกันตาย
    return con


def init():
    with _lock, _con() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS posts(
            tweet_id     TEXT PRIMARY KEY,
            tweet_url    TEXT,
            created_at   TEXT,          -- วันเวลาโพสต์ (ISO ถ้าแปลงได้)
            author       TEXT,
            location     TEXT,
            text         TEXT,          -- แคปชั่นเต็ม
            brand_name   TEXT,          -- ชื่อแบรนด์ที่ AI สกัด
            final_brand  TEXT,          -- แบรนด์จากโดเมนจริงหลัง resolve
            promotions   TEXT,          -- json array
            keywords     TEXT,          -- json array (marketing_keywords)
            cta          TEXT,
            url_landing  TEXT,          -- URL จากแคปชั่น (มักเป็น t.co)
            url_landing_2 TEXT,         -- URL จาก bio/description
            final_url    TEXT,          -- ปลายทางจริงหลังตามลิงก์
            line_id      TEXT,          -- Line@ ถ้าเจอในแคปชั่น
            preview_img  TEXT,          -- รูปแนบทวีต
            video_url    TEXT,          -- วิดีโอแนบทวีต (mp4)
            is_gambling  INTEGER DEFAULT 1,
            search_term  TEXT,
            ingested_at  INTEGER,
            raw          TEXT )""")     # raw = ผลสกัดดิบเผื่อดีบัก
        con.execute("""CREATE TABLE IF NOT EXISTS keywords(
            term      TEXT PRIMARY KEY,
            source    TEXT DEFAULT 'seed',   -- seed = ตั้งเอง, ai = โผล่จาก marketing_keywords
            active    INTEGER DEFAULT 1,
            added_at  INTEGER,
            last_run  INTEGER DEFAULT 0,
            hits      INTEGER DEFAULT 0 )""")
        con.execute("""CREATE TABLE IF NOT EXISTS runs(
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            term      TEXT,
            started   INTEGER,
            scraped   INTEGER DEFAULT 0,   -- ทวีตที่ดึงมา
            added     INTEGER DEFAULT 0,   -- บันทึกใหม่จริง
            skipped   INTEGER DEFAULT 0,   -- ซ้ำ/ไม่ใช่พนัน
            note      TEXT )""")
        # created_at เก็บเป็นสตริงแบบทวิตเตอร์ ("Tue Sep 01 16:57:30 +0000 2026") เรียงเป็น text ไม่ได้
        # -> เก็บ epoch ไว้อีกคอลัมน์เพื่อเรียง "วันที่โพสต์ล่าสุด" ได้จริง
        cols = {r["name"] for r in con.execute("PRAGMA table_info(posts)")}
        if "created_ts" not in cols:
            con.execute("ALTER TABLE posts ADD COLUMN created_ts INTEGER")
        if "reply_to" not in cols:
            con.execute("ALTER TABLE posts ADD COLUMN reply_to TEXT")
        # ปลายทางจริงหลังทะลุหน้า bio-link (heylink/linktr.ee/hopp.bio ไม่ใช่ชื่อแบรนด์)
        if "target_url" not in cols:
            con.execute("ALTER TABLE posts ADD COLUMN target_url TEXT")
            con.execute("ALTER TABLE posts ADD COLUMN target_brand TEXT")
        # เวลาที่ระบบแตะแถวนี้ล่าสุด (เก็บใหม่ / resolve เจอโดเมน / ทะลุ bio-link)
        # ingested_at ใช้แทนไม่ได้ เพราะ resolve แก้แถวเดิมโดยไม่ถือว่าเป็นการเก็บใหม่
        if "updated_at" not in cols:
            con.execute("ALTER TABLE posts ADD COLUMN updated_at INTEGER")
            con.execute("UPDATE posts SET updated_at=ingested_at WHERE updated_at IS NULL")
            con.execute("CREATE INDEX IF NOT EXISTS ix_posts_upd ON posts(updated_at DESC)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_posts_ing ON posts(ingested_at DESC)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_posts_ts ON posts(created_ts DESC)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_posts_brand ON posts(final_brand)")
        need_ts = con.execute("SELECT 1 FROM posts WHERE created_ts IS NULL LIMIT 1").fetchone()
    if need_ts:
        backfill_ts()          # แถวเก่าที่บันทึกก่อนมีคอลัมน์นี้ — เติมให้อัตโนมัติครั้งเดียว


def _j(v):
    return json.dumps(v if v is not None else [], ensure_ascii=False)


def to_ts(created_at):
    """'Tue Sep 01 16:57:30 +0000 2026' (รูปแบบทวิตเตอร์) หรือ ISO -> epoch · แปลงไม่ได้คืน None"""
    s = (created_at or "").strip()
    if not s:
        return None
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            d = datetime.strptime(s.replace("Z", "+0000"), fmt)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return int(d.timestamp())
        except ValueError:
            continue
    return None


def backfill_ts():
    """เติม created_ts ให้แถวที่บันทึกไว้ก่อนมีคอลัมน์นี้ — คืนจำนวนแถวที่เติม"""
    with _lock, _con() as con:
        rows = con.execute("SELECT tweet_id, created_at FROM posts WHERE created_ts IS NULL").fetchall()
        n = 0
        for r in rows:
            ts = to_ts(r["created_at"])
            if ts:
                con.execute("UPDATE posts SET created_ts=? WHERE tweet_id=?", (ts, r["tweet_id"]))
                n += 1
    return n


# ---------------------------------------------------------------- posts
def post_exists(tweet_id):
    with _con() as con:
        return con.execute("SELECT 1 FROM posts WHERE tweet_id=?", (str(tweet_id),)).fetchone() is not None


def upsert_post(p):
    """p = dict ครบทุกคอลัมน์ (ยกเว้น ingested_at ที่เติมให้) — คืน True ถ้าเป็นแถวใหม่"""
    new = not post_exists(p.get("tweet_id"))
    with _lock, _con() as con:
        con.execute("""INSERT INTO posts(
            tweet_id,tweet_url,created_at,author,location,text,brand_name,final_brand,
            promotions,keywords,cta,url_landing,url_landing_2,final_url,line_id,
            preview_img,video_url,is_gambling,search_term,ingested_at,raw,created_ts,reply_to,
            updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(tweet_id) DO UPDATE SET
            final_brand=excluded.final_brand, final_url=excluded.final_url,
            brand_name=excluded.brand_name, promotions=excluded.promotions,
            keywords=excluded.keywords, cta=excluded.cta,
            url_landing=excluded.url_landing, url_landing_2=excluded.url_landing_2,
            line_id=excluded.line_id, is_gambling=excluded.is_gambling,
            updated_at=excluded.updated_at""",
            (str(p.get("tweet_id")), p.get("tweet_url"), p.get("created_at"), p.get("author"),
             p.get("location"), p.get("text"), p.get("brand_name"), p.get("final_brand"),
             _j(p.get("promotions")), _j(p.get("keywords")), p.get("cta"),
             p.get("url_landing"), p.get("url_landing_2"), p.get("final_url"), p.get("line_id"),
             p.get("preview_img"), p.get("video_url"), 1 if p.get("is_gambling", True) else 0,
             p.get("search_term"), int(time.time()), _j(p.get("raw")),
             to_ts(p.get("created_at")), p.get("reply_to"), int(time.time())))
    return new


def pending_landing(limit=None):
    """โพสต์พนันที่ยังไม่รู้โดเมนจริง แต่มีลิงก์ให้ตามต่อ (ปุ่ม 'ตามโดเมนที่ค้าง' ใช้)"""
    q = ("SELECT tweet_id, url_landing FROM posts "
         "WHERE is_gambling=1 AND (final_url IS NULL OR final_url='') "
         "AND url_landing IS NOT NULL AND url_landing!='' ORDER BY ingested_at DESC")
    if limit:
        q += " LIMIT %d" % int(limit)
    with _con() as con:
        return [dict(r) for r in con.execute(q)]


def bio_pages():
    """หน้า bio-link ที่ยังไม่รู้ปลายทาง -> {final_url: [tweet_id, ...]} (ให้ pipeline คัดต่อ)"""
    with _con() as con:
        rows = con.execute(
            "SELECT tweet_id, final_url FROM posts WHERE final_url IS NOT NULL AND final_url!='' "
            "AND (target_url IS NULL OR target_url='')").fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["final_url"], []).append(r["tweet_id"])
    return out


def set_target(tweet_ids, target_url, target_brand):
    with _lock, _con() as con:
        now = int(time.time())
        return sum(con.execute(
            "UPDATE posts SET target_url=?, target_brand=?, updated_at=? WHERE tweet_id=?",
            (target_url, target_brand, now, str(t))).rowcount for t in tweet_ids)


def set_final(tweet_id, final_url, final_brand):
    with _lock, _con() as con:
        return con.execute(
            "UPDATE posts SET final_url=?, final_brand=?, updated_at=? WHERE tweet_id=?",
            (final_url, final_brand, int(time.time()), str(tweet_id))).rowcount


def recent_posts(limit=3000, since=None):
    """since = epoch -> คืนเฉพาะแถวที่เก็บเข้ามาหลังจากนั้น (หน้าเว็บ poll แบบเพิ่มทีละนิด
    ไม่ต้องโหลดใหม่ทั้งก้อนทุก 6 วินาที)"""
    q = "SELECT * FROM posts"
    args = []
    if since:
        q += " WHERE COALESCE(updated_at, ingested_at) > ?"
        args.append(int(since))
    q += " ORDER BY COALESCE(created_ts, ingested_at) DESC LIMIT ?"
    args.append(int(limit))
    with _con() as con:
        rows = con.execute(q, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("promotions", "keywords"):
            try:
                d[k] = json.loads(d[k] or "[]")
            except Exception:
                d[k] = []
        d.pop("raw", None)
        out.append(d)
    return out


# ---------------------------------------------------------------- keywords
def add_keywords(terms, source="ai", active=1):
    """เติมคีย์เวิร์ดใหม่ (ไม่ทับของเดิม) — คืนจำนวนที่เพิ่มจริง
    active=0 ใช้กับคำที่ AI สกัดมา: เข้าคลังไว้ให้เลือกเปิดเอง ไม่ยิงสแครปอัตโนมัติ"""
    n = 0
    now = int(time.time())
    with _lock, _con() as con:
        for t in terms:
            t = (t or "").strip()
            if not t or len(t) > 80:
                continue
            cur = con.execute(
                "INSERT OR IGNORE INTO keywords(term,source,active,added_at) VALUES(?,?,?,?)",
                (t, source, 1 if active else 0, now))
            n += cur.rowcount
    return n


def set_keywords_active(terms, active):
    """เปิด/ปิดหลายคำรวดเดียว (หน้าคลังคีย์เวิร์ดใช้) — คืนจำนวนที่เปลี่ยนจริง"""
    v = 1 if active else 0
    with _lock, _con() as con:
        return sum(con.execute("UPDATE keywords SET active=? WHERE term=? AND active!=?",
                               (v, t, v)).rowcount for t in terms)


def del_keywords(terms):
    """ลบหลายคำรวดเดียว — โพสต์ที่เก็บไว้แล้วไม่แตะ"""
    with _lock, _con() as con:
        return sum(con.execute("DELETE FROM keywords WHERE term=?", (t,)).rowcount for t in terms)


def active_keywords():
    with _con() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM keywords WHERE active=1 ORDER BY last_run ASC, added_at ASC")]


def all_keywords():
    with _con() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM keywords ORDER BY hits DESC, added_at DESC")]


def set_keyword(term, active=None):
    with _lock, _con() as con:
        if active is not None:
            con.execute("UPDATE keywords SET active=? WHERE term=?", (1 if active else 0, term))


def del_keyword(term):
    """ลบคีย์เวิร์ดออกจากคลัง — โพสต์ที่เก็บไว้แล้วไม่แตะ (ยังดูย้อนหลังได้)"""
    with _lock, _con() as con:
        return con.execute("DELETE FROM keywords WHERE term=?", (term,)).rowcount


def mark_keyword_run(term, hits_added):
    with _lock, _con() as con:
        con.execute("UPDATE keywords SET last_run=?, hits=hits+? WHERE term=?",
                    (int(time.time()), int(hits_added), term))


# ---------------------------------------------------------------- runs / stats
def log_run(term, scraped, added, skipped, note=""):
    with _lock, _con() as con:
        con.execute("INSERT INTO runs(term,started,scraped,added,skipped,note) VALUES(?,?,?,?,?,?)",
                    (term, int(time.time()), scraped, added, skipped, note))


def stats():
    with _con() as con:
        total = con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        gambling = con.execute("SELECT COUNT(*) FROM posts WHERE is_gambling=1").fetchone()[0]
        brands = con.execute(
            "SELECT COUNT(DISTINCT final_brand) FROM posts WHERE final_brand IS NOT NULL AND final_brand!=''").fetchone()[0]
        kw = con.execute("SELECT COUNT(*) FROM keywords WHERE active=1").fetchone()[0]
        last = con.execute("SELECT MAX(ingested_at) FROM posts").fetchone()[0]
    return {"total": total, "gambling": gambling, "not_gambling": total - gambling,
            "brands": brands, "keywords": kw, "last_ingest": last}


def purge(days):
    if not days or int(days) <= 0:
        return 0
    cut = int(time.time()) - int(days) * 86400
    with _lock, _con() as con:
        cur = con.execute("DELETE FROM posts WHERE ingested_at < ?", (cut,))
        return cur.rowcount
