#!/usr/bin/env python3
"""
run.py — สั่ง pipeline จาก command line (ไม่ต้องเปิดเซิร์ฟเวอร์)

    python run.py seed "สล็อตเว็บตรง" "เครดิตฟรี" "บาคาร่า"   # เพิ่มคีย์เวิร์ดตั้งต้น
    python run.py kw   "สล็อตเว็บตรง"                        # สแครปคีย์เวิร์ดเดียว
    python run.py all                                        # สแครปทุกคีย์เวิร์ดที่ active
    python run.py list                                       # ดูคลังคีย์เวิร์ด
    python run.py stats                                      # สรุปตัวเลขใน DB
"""

import sys

import db
import pipeline


def main(argv):
    db.init()
    if not argv:
        print(__doc__)
        return
    cmd = argv[0]

    if cmd == "seed":
        n = db.add_keywords(argv[1:], source="seed")
        print("เพิ่มคีย์เวิร์ดตั้งต้น %d คำ (จากที่ส่งมา %d)" % (n, len(argv) - 1))
    elif cmd == "kw" and len(argv) > 1:
        pipeline.run_keyword(argv[1])
    elif cmd == "all":
        res = pipeline.run_all()
        tot = sum(r.get("added", 0) for r in res)
        print("รวมบันทึกใหม่ %d แถว จาก %d คีย์เวิร์ด" % (tot, len(res)))
    elif cmd == "list":
        for k in db.all_keywords():
            print("  [%s] %-30s hits=%-4d %s" % (
                "on " if k["active"] else "off", k["term"], k["hits"], k["source"]))
    elif cmd == "stats":
        for k, v in db.stats().items():
            print("  %-14s %s" % (k, v))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
