# gunicorn.conf.py — Production configuration for Athena-X
# Loaded automatically when gunicorn is started from start.sh

import multiprocessing
import os

# ── Network ──────────────────────────────────────────────────────────────────
bind        = f"0.0.0.0:{os.environ.get('ATHENA_PORT', '5443')}"
certfile    = "cert.pem"
keyfile     = "key.pem"

# ── Workers ───────────────────────────────────────────────────────────────────
# Rule of thumb: 2–4 × CPU cores for I/O-bound apps
workers     = min(multiprocessing.cpu_count() * 2 + 1, 9)
worker_class = "sync"           # sync is best for SQLite (avoids write contention)
threads     = 2                 # 2 threads per worker for light concurrency
timeout     = 120               # kill workers that hang > 2 min
keepalive   = 5

# ── Logging ───────────────────────────────────────────────────────────────────
accesslog   = "logs/access.log"
errorlog    = "logs/error.log"
loglevel    = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s %(D)sµs'

# ── Process ───────────────────────────────────────────────────────────────────
pidfile     = "athena.pid"
daemon      = False             # keep False — systemd/supervisor manages the process

# ── Security ──────────────────────────────────────────────────────────────────
limit_request_line   = 4094
limit_request_fields = 100
forwarded_allow_ips  = "*"
