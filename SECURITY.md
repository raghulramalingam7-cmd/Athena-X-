# 🔒 Athena-X Security Guide

## Quick Start (Mobile + Multi-User)

```bash
pip install -r requirements.txt
python app.py
```

Open on **any phone or tablet** on the same Wi-Fi:
```
http://<your-computer-ip>:5000
```

To find your IP (Windows: `ipconfig`, Mac/Linux: `ifconfig` or `ip a`)

---

## 👑 Creating an Admin Account

1. Open `http://<your-ip>:5000/admin/signup`
2. Enter the **Admin Security Code**:

```
Ath3na@Gully#2026!
```

3. Fill in your username, email, and password
4. Log in at `/admin/login`

> **Change the code** before sharing the app! Set an environment variable:
> ```bash
> # Linux / Mac
> export ATHENA_ADMIN_KEY="YourNewSecretCode123!"
> python app.py
>
> # Windows
> set ATHENA_ADMIN_KEY=YourNewSecretCode123!
> python app.py
> ```

---

## 👥 Multi-User Access

| Role  | Sign Up URL         | Login URL          | Can Do                          |
|-------|---------------------|--------------------|---------------------------------|
| User  | `/register`         | `/login`           | View events, register for games |
| Admin | `/admin/signup` *   | `/admin/login`     | Score matches, manage events    |

*Requires the Admin Security Code above.

---

## 🛡️ Security Features Added (v6-secured)

| Feature                  | Details                                               |
|--------------------------|-------------------------------------------------------|
| **Secure secret key**    | Auto-generated & saved to `.secret_key` file          |
| **Session hardening**    | HttpOnly cookie, SameSite=Lax, 8-hour auto-expiry     |
| **Rate limiting**        | 5 failed logins → 10-minute lockout (per IP)          |
| **Security headers**     | X-Frame-Options, X-Content-Type, X-XSS-Protection     |
| **Password hashing**     | Werkzeug `generate_password_hash` (PBKDF2-HMAC-SHA256)|
| **Admin key via env var**| `ATHENA_ADMIN_KEY` env var — no hardcoded secrets     |
| **Mobile ready**         | `host=0.0.0.0` — phones on same Wi-Fi can connect    |
| **debug=False default**  | Safe for production; enable via `ATHENA_DEBUG=1`      |

---

## ⚙️ Environment Variables

| Variable           | Default                   | Purpose                         |
|--------------------|---------------------------|---------------------------------|
| `ATHENA_SECRET_KEY`| Auto-generated            | Flask session secret key        |
| `ATHENA_ADMIN_KEY` | `Ath3na@Gully#2026!`      | Code required to create admins  |
| `ATHENA_PORT`      | `5000`                    | Port to run the server on       |
| `ATHENA_DEBUG`     | `0`                       | Set to `1` to enable debug mode |

---

## 📱 Accessing From Mobile

1. Make sure your phone and computer are on the **same Wi-Fi**
2. Run `python app.py` — it will print your server URL
3. On your phone, open the browser and go to `http://<computer-ip>:5000`

For internet access (outside your home/office), use a tunnel:
```bash
# Install ngrok from https://ngrok.com, then:
ngrok http 5000
# Copy the https://... URL — share it with anyone!
```

---

## 🔐 For Production Deployment

1. Set `ATHENA_SECRET_KEY` to a long random value
2. Set `ATHENA_ADMIN_KEY` to a strong, unique passphrase
3. Run behind **HTTPS** (Nginx + Let's Encrypt) and set `SESSION_COOKIE_SECURE=True` in `app.py`
4. Use a production WSGI server: `gunicorn app:app --workers 4`
