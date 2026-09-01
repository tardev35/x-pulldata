#!/usr/bin/env python3
"""
server.py — เว็บเซิร์ฟเวอร์ x-pulldata (Python stdlib ล้วน เหมือน ct-live)

    python server.py
    เปิด http://localhost:8770

เสิร์ฟหน้า index.html + API + (ถ้า COLLECTOR=1) วนสแครปเองเป็นรอบ
สั่ง scrape จากปุ่มบนหน้าเว็บได้ (POST /api/run) — รันในเธรดเบื้องหลัง หน้าเว็บ poll ดูผลสด
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import db
import pipeline
from config import ENV, PORT

HERE = Path(__file__).parent

# สถานะการรันเบื้องหลัง (ปุ่ม "ดึงข้อมูล" บนหน้าเว็บ) — เก็บ log ล่าสุดไว้ให้ UI poll
_run = {"busy": False, "started": 0, "term": "", "log": []}
_run_lock = threading.Lock()


def _log(msg):
    line = time.strftime("%H:%M:%S ") + str(msg)
    print(line)
    with _run_lock:
        _run["log"].append(line)
        _run["log"] = _run["log"][-200:]


def _run_bg(term):
    with _run_lock:
        if _run["busy"]:
            return False
        _run.update(busy=True, started=int(time.time()), term=term or "(ทุกคีย์เวิร์ด)", log=[])

    def work():
        try:
            if term:
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
                self._json({"rows": db.recent_posts()})
            except Exception as ex:
                self._json({"rows": [], "error": str(ex)})
        elif p == "/api/keywords":
            self._json({"rows": db.all_keywords()})
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
            ok = _run_bg(term)
            self._json({"ok": ok, "busy": not ok, "term": term})
        elif self.path == "/api/keyword":
            term = (payload.get("term") or "").strip()
            if not term:
                self._json({"error": "ไม่มี term"}, 400)
                return
            if "active" in payload:
                db.set_keyword(term, bool(payload["active"]))
            else:
                db.add_keywords([term], source="seed")
            self._json({"ok": True})
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")


# ---------------------------------------------------------------- collector
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
    n = db.purge(ENV.get("retain_days"))
    if n:
        print("purge: ลบโพสต์เก่ากว่า %s วัน ออก %d แถว" % (ENV.get("retain_days"), n))
    print("x-pulldata — สแครป X -> สกัด AI -> หาโดเมนจริง -> โชว์บนหน้าเว็บ")
    print("  Apify token : %s" % ("ตั้งแล้ว" if ENV["apify_token"] else "ยังไม่ตั้ง (ดู .env)"))
    print("  OpenRouter  : %s (%s)" % ("ตั้งแล้ว" if ENV["or_key"] else "ยังไม่ตั้ง", ENV["model"]))
    print("  proxy       : %s" % ("ตั้งแล้ว (residential)" if ENV.get("proxy_url") else "Apify proxy"))
    print("  keywords    : %d คำ active" % len(db.active_keywords()))
    print("\n  เปิดเบราว์เซอร์ที่  ->  http://localhost:%d" % PORT)
    print("  หยุดด้วย Ctrl-C\n")
    if ENV.get("collect") == "1":
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
