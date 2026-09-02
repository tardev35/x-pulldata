# DEPLOY — x-pulldata ขึ้น VPS

VPS: `45.32.122.181` · โดเมน: **https://x-finder.yoalink.com** · **deploy แล้ว 3 ก.ย. 2026**

> เครื่องนี้ไม่ได้มีแค่เรา — เป็น `PBN-1` ที่รัน **nginx + เว็บ WordPress 321 ไซต์** และ
> `ct-live` (127.0.0.1:8765) อยู่ก่อนแล้ว ทุกอย่างในคู่มือนี้จึงเป็นการ **เพิ่ม** ไม่ใช่ทับของเดิม
> (อย่าลบ vhost อื่น อย่าแตะ default อย่าเปลี่ยน timezone ทั้งเครื่อง)

โปรเจกต์นี้ใช้ **Python stdlib ล้วน ไม่ต้อง pip install อะไรเลย** ของที่ต้องลงบนเซิร์ฟเวอร์
มีแค่ nginx (ทำ reverse proxy + รหัสผ่าน + TLS) เท่านั้น

---

## ⚠️ เรื่องที่ต้องตัดสินใจก่อนเปิดสู่อินเทอร์เน็ต

**หน้าเว็บนี้ไม่มีระบบล็อกอิน** และปุ่มบนหน้าเว็บ *ใช้เงินจริง* (Apify + OpenRouter)
ถ้าเปิดโล่ง ๆ ใครเจอ URL ก็กด "ดึงจาก X" รัวได้ → บิลบาน

แผนนี้จึงบังคับ **รหัสผ่านชั้น nginx (Basic Auth)** ไว้ตั้งแต่ต้น ตัวแอปเองยังผูกกับ
`127.0.0.1:8770` เหมือนเดิม เข้าจากภายนอกตรง ๆ ไม่ได้ ต้องผ่าน nginx เท่านั้น

---

## 1) เตรียมเครื่อง (รันบน VPS)

```bash
ssh root@45.32.122.181
python3 -V                    # ได้ 3.10.12 — พอแล้ว ไม่ต้องลง pip อะไรเลย

# ผู้ใช้แยกสำหรับรันแอป (ไม่รันด้วย root)
adduser --system --group --home /opt/x-pulldata xpull
```

**ไม่ต้องทำ** บนเครื่องนี้ (มีอยู่แล้ว): nginx, certbot, ufw (เปิด 22/80/443 ไว้แล้ว)

**ห้ามทำ:** `timedatectl set-timezone` — เครื่องเป็น UTC และมีเว็บอื่น 321 ไซต์ใช้อยู่
เราตั้ง `TZ=Asia/Bangkok` เฉพาะโปรเซสของเราใน systemd แทน (ดูขั้นที่ 3)

## 2) ส่งโค้ดขึ้นเครื่อง

**ทางเลือก A — rsync จากเครื่องตัวเอง (ง่ายสุด ไม่ต้องผูก GitLab กับ VPS)**
```bash
# รันบนเครื่อง Windows (Git Bash)
cd /c/x-pulldata
rsync -av --exclude data/ --exclude .git/ --exclude __pycache__/ --exclude .env \
  ./ root@45.32.122.181:/opt/x-pulldata/
```

**ทางเลือก B — clone จาก GitLab** (ต้องสร้าง Deploy Token ใน GitLab ก่อน)
```bash
git clone https://<deploy-token-user>:<token>@gitlab.com/tardev-group/x-pulldata.git /opt/x-pulldata
```

จากนั้นส่งไฟล์ลับแยกต่างหาก (อย่าใส่ใน git เด็ดขาด):
```bash
scp .env root@45.32.122.181:/opt/x-pulldata/.env
ssh root@45.32.122.181 'chmod 600 /opt/x-pulldata/.env && chown -R xpull:xpull /opt/x-pulldata'
```

`.env` ที่ควรใช้บนเซิร์ฟเวอร์ (ต่างจากเครื่องตัวเองเล็กน้อย):
```ini
COLLECTOR=1          # ให้เก็บเองอัตโนมัติ (เครื่องเปิด 24 ชม. แล้ว ใช้ประโยชน์ให้คุ้ม)
COLLECT_CYCLE=1800   # ทุก 30 นาที ทีละ 5 คำ
RETAIN_DAYS=3        # เก็บย้อนหลัง 3 วัน เท่าที่ตกลงกันไว้
SCRAPE_WORKERS=5
LLM_WORKERS=16
MAX_ITEMS=200
```

## 3) ทำเป็น service (systemd)

```bash
cat >/etc/systemd/system/x-pulldata.service <<'EOF'
[Unit]
Description=x-pulldata (social listening iGaming บน X)
After=network-online.target

[Service]
Type=simple
User=xpull
WorkingDirectory=/opt/x-pulldata
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
ExecStart=/usr/bin/python3 /opt/x-pulldata/server.py
Restart=always
RestartSec=5
# กันพลาด: เขียนได้เฉพาะโฟลเดอร์ตัวเอง
ProtectSystem=strict
ReadWritePaths=/opt/x-pulldata
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now x-pulldata
systemctl status x-pulldata --no-pager
journalctl -u x-pulldata -f          # ดู log สด
```

## 4) nginx + รหัสผ่าน + TLS

TLS จัดการที่ **Cloudflare** (โดเมนชี้ CF proxy + เปิด Always Use HTTPS อยู่แล้ว)
**ไม่ใช้ certbot** ที่ต้นทาง

```bash
htpasswd -bc /etc/nginx/.htpasswd-xpull admin 'รหัสผ่าน'

cat >/etc/nginx/sites-available/x-pulldata <<'EOF'
server {
    listen 80;
    listen [::]:80;
    server_name x-finder.yoalink.com;

    location / {
        auth_basic "x-pulldata";
        auth_basic_user_file /etc/nginx/.htpasswd-xpull;
        proxy_pass http://127.0.0.1:8770;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 900s;    # งานดึง/resolve นาน อย่าตัดกลางคัน
        proxy_buffering off;        # log สดต้องไหลทันที
    }
    access_log /var/log/nginx/x-pulldata.access.log;
    error_log  /var/log/nginx/x-pulldata.error.log;
}
EOF

ln -sf /etc/nginx/sites-available/x-pulldata /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx      # ต้องเช็ค exit code จริง อย่าต่อท้ายด้วย | tail
```

### กับดัก 2 อันที่เจอจริงตอน deploy
1. **ห้าม redirect http -> https ที่ต้นทาง** — Cloudflare โหมด Flexible ต่อเข้ามาทาง `:80`
   ถ้าเจอ 301 มันจะเด้งกลับ CF แล้ววนไม่รู้จบ (ERR_TOO_MANY_REDIRECTS)
   ผู้ใช้ยังได้ HTTPS อยู่ดีเพราะ CF บังคับให้เอง
2. **nginx 1.18 ไม่รู้จัก `http2 on;`** (ของ 1.25+) ใส่ไปแล้ว `nginx -t` พัง เลยไม่ reload
   และของเก่ายังทำงานอยู่ — ดูเผิน ๆ เหมือนแก้แล้วไม่มีผล

> อยากให้ขา Cloudflare→เครื่องเข้ารหัสด้วย: สร้าง **Origin Certificate** ใน Cloudflare
> (ฟรี 15 ปี) วางที่เครื่อง เพิ่ม block `listen 443 ssl;` แล้วสลับ SSL mode เป็น Full (strict)

## 6) สำรองข้อมูล

```bash
mkdir -p /opt/backups
cat >/etc/cron.daily/x-pulldata-backup <<'EOF'
#!/bin/sh
d=$(date +%Y%m%d)
sqlite3 /opt/x-pulldata/data/pulldata.db ".backup /opt/backups/pulldata-$d.db" 2>/dev/null \
  || cp /opt/x-pulldata/data/pulldata.db /opt/backups/pulldata-$d.db
find /opt/backups -name 'pulldata-*.db' -mtime +14 -delete
EOF
chmod +x /etc/cron.daily/x-pulldata-backup
```
(ถ้าไม่มี `sqlite3` ให้ `apt install -y sqlite3` — ตัวแอปไม่ต้องใช้ แต่ backup แบบ hot copy ปลอดภัยกว่า)

## 7) อัปเดตโค้ดรอบถัดไป

```bash
# ทางเลือก A (rsync จากเครื่องตัวเอง)
rsync -av --exclude data/ --exclude .git/ --exclude __pycache__/ --exclude .env \
  ./ root@45.32.122.181:/opt/x-pulldata/
ssh root@45.32.122.181 'chown -R xpull:xpull /opt/x-pulldata && systemctl restart x-pulldata'

# ทางเลือก B (git)
ssh root@45.32.122.181 'cd /opt/x-pulldata && git pull && systemctl restart x-pulldata'
```
DB ไม่ถูกแตะ (`data/` ไม่อยู่ใน rsync/git) · migration ของ schema ทำเองอัตโนมัติตอน `db.init()`

---

## ตรวจก่อนบอกว่าเสร็จ

- [ ] `systemctl status x-pulldata` = active (running)
- [ ] `curl -u admin:xxx http://127.0.0.1:8770/api/status` บน VPS ตอบ JSON
- [ ] เปิดหน้าเว็บจากข้างนอกแล้วโดนถามรหัสผ่าน (ไม่ใช่เข้าได้เลย)
- [ ] `curl http://45.32.122.181:8770` จากข้างนอก **ต้องต่อไม่ติด** (แอปผูก 127.0.0.1 + ufw ปิด)
- [ ] กด "ดึงจาก X" 1 คำแล้วมีโพสต์เข้า + log ไหลบนหน้าเว็บ
- [ ] `systemctl show x-pulldata -p Environment` มี `TZ=Asia/Bangkok`
- [ ] สุ่มเปิดเว็บ PBN เดิมสัก 2 ไซต์ ต้องยัง 200 (ไม่ไปทับ config ใคร)

## จุดที่ต้องระวังบน Linux (ต่างจากเครื่อง Windows)

- **เวลาเครื่อง** — `posts_by_day()` และ `purge` ใช้ `localtime` ของ SQLite เครื่องนี้เป็น UTC
  จึงต้องตั้ง `Environment=TZ=Asia/Bangkok` ใน systemd (แค่โปรเซสเรา ไม่กระทบเว็บอื่น)
- **ssl.enum_certificates** ใช้ได้เฉพาะ Windows — โค้ดมี `hasattr` ครอบไว้แล้ว บน Linux
  จะใช้ CA ของระบบตามปกติ (ดีกว่าเดิมด้วยซ้ำ ปัญหา verify ของ bit.ly น่าจะหายไป)
- **proxy** — `PROXY_URL` (webshare) ใช้ได้เหมือนเดิม ไม่ผูกกับเครื่อง
- **หน่วยความจำ** — `LLM_WORKERS=16` + `SCRAPE_WORKERS=5` กินแรมไม่เกิน ~200MB
  แต่ถ้า VPS เล็กกว่า 1GB ให้ลด `SCRAPE_WORKERS` เหลือ 2-3
