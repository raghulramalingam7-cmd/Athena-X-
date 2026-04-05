# 🏆 Athena-X — Production Deployment Guide

## ⚡ Quick Start (2 steps)

```bash
# 1. Enter the folder
cd athena-v4-prod

# 2. Run
bash start.sh
```

Open **`https://YOUR-IP:5443`** on any device on the same Wi-Fi.
First visit: click **Advanced → Proceed** to accept the self-signed certificate.

---

## What's Different in This Build

| | `python app.py` (old) | `bash start.sh` (production) |
|---|---|---|
| Server | Flask dev server | **Gunicorn** (multi-worker) |
| Protocol | HTTP | **HTTPS / TLS** |
| Concurrent users | 1 at a time | Many |
| Cookie security | Not secured | `Secure` + `HttpOnly` + `SameSite` |
| HSTS | ❌ | ✅ Browsers enforce HTTPS for 1 year |
| SSL cert | None | Auto-generated self-signed (3 years) |
| Logs | Console only | `logs/access.log` + `logs/error.log` |
| Warning | ⚠️ "development server" | ✅ None |

---

## Files

```
athena-v4-prod/
├── start.sh            ← Run this to start (everything auto-handled)
├── app.py              ← Flask application
├── gunicorn.conf.py    ← Worker / SSL / logging config
├── generate_cert.py    ← SSL cert generator (called by start.sh)
├── athena.service      ← systemd unit for Linux auto-start on boot
├── requirements.txt    ← flask, gunicorn, cryptography, werkzeug
├── gully_sports.db     ← SQLite database
├── cert.pem            ← TLS certificate (auto-created, 3 years)
├── key.pem             ← TLS private key  (auto-created — keep private!)
├── logs/
│   ├── access.log
│   └── error.log
├── templates/
└── static/
```

---

## Environment Variables

```bash
# All optional — defaults shown
export ATHENA_PORT=5443               # HTTPS port
export ATHENA_ADMIN_KEY=Ath3na@Gully#2026!   # Admin signup key
export ATHENA_SECRET_KEY=...          # Flask session secret (auto if not set)
bash start.sh
```

---

## Auto-Start on Boot (Linux)

```bash
# Edit athena.service: set your username and path, then:
sudo cp athena.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable athena    # start on boot
sudo systemctl start athena     # start now

# Useful commands
sudo systemctl status athena
sudo journalctl -u athena -f    # live logs
```

---

## SSL Certificate

### Self-signed (default — works immediately)
- Created automatically on first `bash start.sh`
- Valid 3 years, includes your hostname and LAN IP
- Browser shows a one-time warning → click **Advanced → Proceed**
- Fine for LAN / college events / demos

### Let's Encrypt (optional — no browser warning)
Only if you have a public domain name:
```bash
sudo certbot certonly --standalone -d yourdomain.com
cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem cert.pem
cp /etc/letsencrypt/live/yourdomain.com/privkey.pem   key.pem
bash start.sh
```

---

## Using Port 443 (Standard HTTPS)

```bash
sudo ATHENA_PORT=443 bash start.sh
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Address already in use` | Change port: `ATHENA_PORT=5444 bash start.sh` |
| `gunicorn: not found` | Run `pip install gunicorn` |
| Mobile can't connect | Ensure phone is on the same Wi-Fi |
| Firewall blocks port | `sudo ufw allow 5443` |
| Cert error on restart | Delete `cert.pem` + `key.pem`, rerun `start.sh` |

---

## Security Notes

- `key.pem` is your private TLS key — never share it or commit it to git
- `.secret_key` holds the Flask session secret — keep it private
- All passwords are hashed (bcrypt) in the database
- HSTS is enabled — after first HTTPS visit, browsers will refuse HTTP
