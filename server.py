#!/usr/bin/env python3
"""
server.py — เว็บเซิร์ฟเวอร์ x-pulldata (Python stdlib ล้วน เหมือน ct-live)

    python server.py
    เปิด http://localhost:8770

เสิร์ฟหน้า index.html + API + (ถ้า COLLECTOR=1) วนสแครปเองเป็นรอบ
สั่ง scrape จากปุ่มบนหน้าเว็บได้ (POST /api/run) — รันในเธรดเบื้องหลัง หน้าเว็บ poll ดูผลสด
"""

import json
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import db
import pipeline
from config import ENV, PORT

HERE = Path(__file__).parent

# คอนโซล Windows เป็น cp874 — คีย์เวิร์ดที่มีอักษรพิเศษ (เช่น 𝙥𝙜) จะทำ print ระเบิดกลางรัน
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# สถานะการรันเบื้องหลัง (ปุ่ม "ดึงข้อมูล" บนหน้าเว็บ) — เก็บ log ล่าสุดไว้ให้ UI poll
_run = {"busy": False, "started": 0, "term": "", "log": []}
_run_lock = threading.Lock()


def _log(msg):
    line = time.strftime("%H:%M:%S ") + str(msg)
    print(line)
    with _run_lock:
        _run["log"].append(line)
        _run["log"] = _run["log"][-200:]


def _run_bg(term, terms=None):
    """term = คำเดียว (เว้นว่าง = ทุกคำที่เปิดใช้) · terms = รายชื่อคำที่เลือกมาจากคลัง"""
    label = ("%d คำที่เลือก" % len(terms)) if terms else (term or "(ทุกคีย์เวิร์ด)")
    with _run_lock:
        if _run["busy"]:
            return False
        _run.update(busy=True, started=int(time.time()), term=label, log=[])

    def work():
        try:
            if terms:
                pipeline.run_terms(terms, log=_log)
            elif term:
                pipeline.run_keyword(term, log=_log)
            else:
                pipeline.run_all(log=_log)
        except Exception as ex:
            _log("[error] %s" % ex)
        finally:
            with _run_lock:
                _run["busy"] = False
            _log("— จบรอบ —")

    threading.Thread(target=work, daemon=True).start()
    return True


def _job_bg(label, fn):
    """งานเบื้องหลังที่ไม่ใช่การสแครป (ตามโดเมน / ทะลุ bio-link) — ใช้ช่องสถานะเดียวกับปุ่มดึง"""
    with _run_lock:
        if _run["busy"]:
            return False
        _run.update(busy=True, started=int(time.time()), term=label, log=[])

    def work():
        try:
            fn(log=_log)
        except Exception as ex:
            _log("[error] %s" % ex)
        finally:
            with _run_lock:
                _run["busy"] = False
            _log("— จบรอบ —")

    threading.Thread(target=work, daemon=True).start()
    return True


# ---------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _json(self, obj, code=200):
        self._send(code, "application/json; charset=utf-8",
                   json.dumps(obj, ensure_ascii=False).encode())

    def do_GET(self):
        p = self.path
        if p == "/" or p.startswith("/index"):
            try:
                self._send(200, "text/html; charset=utf-8", (HERE / "index.html").read_bytes())
            except FileNotFoundError:
                self._send(500, "text/plain; charset=utf-8", "หา index.html ไม่เจอ".encode())
        elif p.startswith("/api/posts"):
            try:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(p).query)
                since = int((qs.get("since") or [0])[0] or 0)
                rows = db.recent_posts(since=since or None)
                self._json({"rows": rows, "since": since,
                            "now": max([(r.get("updated_at") or r.get("ingested_at") or 0)
                                        for r in rows] or [since])})
            except Exception as ex:
                self._json({"rows": [], "error": str(ex)})
        elif p == "/api/keywords":
            self._json({"rows": db.all_keywords()})
        elif p == "/api/posts/days":
            self._json({"rows": db.posts_by_day()})
        elif p == "/api/runs":
            self._json({"rows": db.recent_runs()})
        elif p == "/api/stats":
            self._json(db.stats())
        elif p == "/api/run-status":
            with _run_lock:
                self._json(dict(_run))
        elif p == "/api/status":
            self._json({
                "apify": bool(ENV["apify_token"]),
                "openrouter": bool(ENV["or_key"]),
                "model": ENV["model"],
                "proxy": bool(ENV.get("proxy_url")),
                "collector": (ENV.get("collect")) == "1",
                "port": PORT,
            })
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            payload = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            self._json({"error": "payload ไม่ถูกต้อง"}, 400)
            return

        if self.path == "/api/run":
            term = (payload.get("term") or "").strip()
            terms = [str(t).strip() for t in (payload.get("terms") or []) if str(t).strip()]
            ok = _run_bg(term, terms)
            self._json({"ok": ok, "busy": not ok, "term": term, "n": len(terms)})
        elif self.path == "/api/keyword":
            term = (payload.get("term") or "").strip()
            if not term:
                self._json({"error": "ไม่มี term"}, 400)
                return
            if payload.get("del"):
                n = db.del_keyword(term)
                self._json({"ok": True, "deleted": n})
                return
            if "active" in payload:
                db.set_keyword(term, bool(payload["active"]))
            else:
                db.add_keywords([term], source="seed")
            self._json({"ok": True})
        elif self.path == "/api/resolve-pending":
            ok = _job_bg("(ตามโดเมนที่ค้าง)", pipeline.resolve_pending)
            self._json({"ok": ok, "busy": not ok})
        elif self.path == "/api/resolve-bio":
            ok = _job_bg("(ทะลุ bio-link)", pipeline.resolve_bio)
            self._json({"ok": ok, "busy": not ok})
        elif self.path == "/api/posts/delete":
            # หน้าจัดการโพสต์: ลบเป็นวัน / ก่อนวันที่ / ทั้งหมด (คลังคำค้นหาไม่ถูกแตะ)
            day = (payload.get("day") or "").strip()
            before = (payload.get("before") or "").strip()
            everything = bool(payload.get("all"))
            if not (day or before or everything):
                self._json({"error": "ต้องระบุ day / before / all"}, 400)
                return
            n = db.delete_posts(day=day or None, before=before or None, everything=everything)
            _log("[delete] ลบโพสต์ %d แถว (%s)" % (n, "ทั้งหมด" if everything else (day or "ก่อน " + before)))
            self._json({"ok": True, "deleted": n})
        elif self.path == "/api/keywords/bulk":
            # หน้าคลังคีย์เวิร์ดสั่งทีละหลายคำ (เปิด/ปิด/ลบ) — ยิงทีเดียวไม่ต้องวน 200 request
            terms = [str(t).strip() for t in (payload.get("terms") or []) if str(t).strip()]
            terms = list(dict.fromkeys(terms))
            act = payload.get("action")
            if not terms or act not in ("add", "on", "off", "del"):
                self._json({"error": "ต้องมี terms และ action = add/on/off/del"}, 400)
                return
            if act == "add":
                # เพิ่มเอง = ตั้งใจจะดึงคำนี้ -> คำใหม่เข้าคลังแบบเปิดใช้ทันที
                # คำที่มีอยู่แล้วแต่ปิดไว้ (เช่นที่ AI ใส่มา) ก็เปิดให้ด้วย ไม่ต้องไปหาในคลังเอง
                added = db.add_keywords(terms, source="seed", active=1)
                turned_on = db.set_keywords_active(terms, True)
                self._json({"ok": True, "action": act, "added": added, "turned_on": turned_on,
                            "already": len(terms) - added - turned_on, "terms": terms})
                return
            n = db.del_keywords(terms) if act == "del" else db.set_keywords_active(terms, act == "on")
            self._json({"ok": True, "n": n, "action": act})
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")


def purge_loop():
    """เคลียร์ข้อมูลเก่าเองทุก 6 ชม. — เดิมทำแค่ตอนเปิดเซิร์ฟเวอร์ ถ้าเปิดค้างยาว ๆ ก็ไม่เคยลบเลย"""
    days = ENV.get("retain_days")
    while True:
        try:
            n = db.purge(days)
            if n:
                _log("[purge] ลบโพสต์ที่โพสต์เกิน %s วัน ออก %d แถว" % (days, n))
        except Exception as ex:
            _log("purge loop: %s" % ex)
        time.sleep(6 * 3600)


# ---------------------------------------------------------------- collector
def clock_loop(slots):
    """ดึงทุกคีย์เวิร์ดตามเวลานาฬิกาที่ตั้งไว้ (เช่น 07:00 กับ 19:00)

    ต่างจากโหมดวนทุก N วินาที: รอบนี้กวาด "ทุกคำที่เปิดใช้" ในครั้งเดียว เวลาแน่นอน
    คุมค่าใช้จ่ายได้ตรง ๆ (จำนวนรอบต่อวัน = จำนวนเวลาที่ตั้ง)"""
    _log("collector: เปิดแบบตั้งเวลา — %s (เวลาเครื่อง)" % ", ".join(slots))
    done = set()
    while True:
        try:
            now = time.strftime("%H:%M")
            key = (time.strftime("%Y-%m-%d"), now)
            if now in slots and key not in done and not _run["busy"]:
                done.add(key)
                if len(done) > 20:
                    done = set(list(done)[-10:])
                with _run_lock:
                    _run.update(busy=True, started=int(time.time()),
                                term="(อัตโนมัติ %s)" % now, log=[])
                try:
                    pipeline.run_all(log=_log)
                finally:
                    with _run_lock:
                        _run["busy"] = False
                    _log("— จบรอบอัตโนมัติ —")
        except Exception as ex:
            _log("clock loop: %s" % ex)
        time.sleep(20)


def collector_loop():
    cycle = int(ENV.get("collect_cycle") or 900)        # ค่าเริ่ม 15 นาที (สแครปมีค่าใช้จ่าย)
    batch = 5                                            # หมุนทีละ 5 คีย์เวิร์ด/รอบ
    _log("collector: เปิด — รอบละ %ds ทีละ %d คีย์เวิร์ด" % (cycle, batch))
    while True:
        try:
            if not _run["busy"] and db.active_keywords():
                with _run_lock:
                    _run.update(busy=True, started=int(time.time()), term="(collector)")
                try:
                    pipeline.run_all(limit=batch, log=_log)
                finally:
                    with _run_lock:
                        _run["busy"] = False
        except Exception as ex:
            _log("collector loop: %s" % ex)
        time.sleep(cycle)


def main():
    db.init()
    print("x-pulldata — สแครป X -> สกัด AI -> หาโดเมนจริง -> โชว์บนหน้าเว็บ")
    print("  Apify token : %s" % ("ตั้งแล้ว" if ENV["apify_token"] else "ยังไม่ตั้ง (ดู .env)"))
    print("  OpenRouter  : %s (%s)" % ("ตั้งแล้ว" if ENV["or_key"] else "ยังไม่ตั้ง", ENV["model"]))
    print("  proxy       : %s" % ("ตั้งแล้ว (residential)" if ENV.get("proxy_url") else "Apify proxy"))
    print("  keywords    : %d คำ active" % len(db.active_keywords()))
    print("\n  เปิดเบราว์เซอร์ที่  ->  http://localhost:%d" % PORT)
    print("  หยุดด้วย Ctrl-C\n")
    if int(ENV.get("retain_days") or 0) > 0:
        threading.Thread(target=purge_loop, daemon=True).start()
        print("purge: เปิด — เก็บย้อนหลัง %s วัน (เช็คทุก 6 ชม.)" % ENV.get("retain_days"))
    else:
        print("purge: ปิด (ตั้ง RETAIN_DAYS ใน .env ให้มากกว่า 0)")
    slots = [x.strip() for x in (ENV.get("collect_at") or "").split(",") if x.strip()]
    if ENV.get("collect") == "1" and slots:
        threading.Thread(target=clock_loop, args=(slots,), daemon=True).start()
        print("collector: ตั้งเวลา %s (ทุกคำที่เปิดใช้)" % ", ".join(slots))
    elif ENV.get("collect") == "1":
        threading.Thread(target=collector_loop, daemon=True).start()
    else:
        print("collector: ปิด (ตั้ง COLLECTOR=1 ใน .env ให้สแครปเองอัตโนมัติ)")
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nหยุดแล้ว")


if __name__ == "__main__":
    main()
