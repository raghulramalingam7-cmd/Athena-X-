#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
#  Athena-X Startup Script
#  Usage:
#    bash start.sh          → HTTPS (self-signed cert, port 5443)
#    bash start.sh --http   → HTTP  (plain, port 5000) — for LAN/dev use
#
#  Environment variables (optional):
#    ATHENA_ADMIN_KEY   — Admin signup code       (default: Ath3na@Gully#2026!)
#    ATHENA_PORT        — Port to listen on        (default: 5443 HTTPS / 5000 HTTP)
# ══════════════════════════════════════════════════════════════════════════════

set -e
cd "$(dirname "$0")"

BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

# ── Detect HTTP vs HTTPS mode ─────────────────────────────────────────────────
USE_HTTP=0
for arg in "$@"; do
  [[ "$arg" == "--http" ]] && USE_HTTP=1
done

if [[ "$USE_HTTP" == "1" ]]; then
  export ATHENA_HTTPS=0
  PORT="${ATHENA_PORT:-5000}"
  SCHEME="http"
else
  export ATHENA_HTTPS=1
  PORT="${ATHENA_PORT:-5443}"
  SCHEME="https"
fi

echo ""
echo -e "${BLUE}══════════════════════════════════════════════${NC}"
echo -e "${BLUE}   🏆  Athena-X Sports Platform               ${NC}"
echo -e "${BLUE}══════════════════════════════════════════════${NC}"
echo ""

# ── Step 1: Python check ──────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  echo -e "${RED}❌  python3 not found. Install Python 3.9+${NC}"
  exit 1
fi
echo -e "${GREEN}✅  Python: $(python3 --version)${NC}"

# ── Step 2: Virtual environment ───────────────────────────────────────────────
if [ ! -d ".venv" ]; then
  echo -e "${YELLOW}⚙️   Creating virtual environment...${NC}"
  python3 -m venv .venv
fi
source .venv/bin/activate
echo -e "${GREEN}✅  Virtual environment active${NC}"

# ── Step 3: Install dependencies ──────────────────────────────────────────────
echo -e "${YELLOW}📦  Installing dependencies...${NC}"
pip install -r requirements.txt -q --disable-pip-version-check
echo -e "${GREEN}✅  Dependencies installed${NC}"

# ── Step 4: Create required directories ───────────────────────────────────────
mkdir -p logs
echo -e "${GREEN}✅  Directories ready${NC}"

# ── Step 5: SSL certificate (HTTPS mode only) ─────────────────────────────────
if [[ "$USE_HTTP" == "0" ]]; then
  if [ ! -f "cert.pem" ] || [ ! -f "key.pem" ]; then
    echo -e "${YELLOW}🔐  Generating SSL certificate...${NC}"
    python3 generate_cert.py
  else
    echo -e "${GREEN}✅  SSL certificate found${NC}"
  fi
fi

# ── Step 6: Get local IP ──────────────────────────────────────────────────────
LOCAL_IP=$(python3 -c "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('8.8.8.8',80)); print(s.getsockname()[0]); s.close()" 2>/dev/null || echo "your-ip")

# ── Step 7: Launch ────────────────────────────────────────────────────────────
echo ""
echo -e "${BLUE}══════════════════════════════════════════════${NC}"
if [[ "$USE_HTTP" == "1" ]]; then
  echo -e "  🌐  Mode     : ${YELLOW}HTTP (plain — no cert warning)${NC}"
else
  echo -e "  🔒  Mode     : HTTPS (self-signed cert)"
fi
echo -e "  🌐  Local    : ${GREEN}${SCHEME}://localhost:${PORT}${NC}"
echo -e "  📱  Network  : ${GREEN}${SCHEME}://${LOCAL_IP}:${PORT}${NC}"
echo -e "  📋  Logs     : logs/access.log  |  logs/error.log"
if [[ "$USE_HTTP" == "0" ]]; then
  echo ""
  echo -e "  ${YELLOW}⚠️   First visit: browser will warn 'Not secure'${NC}"
  echo -e "  ${YELLOW}    Click 'Advanced' → 'Proceed to ${LOCAL_IP}' to continue${NC}"
  echo -e "  ${YELLOW}    Or restart with:  bash start.sh --http${NC}"
fi
echo -e "${BLUE}══════════════════════════════════════════════${NC}"
echo ""
echo -e "  Press ${RED}Ctrl+C${NC} to stop the server"
echo ""

if [[ "$USE_HTTP" == "1" ]]; then
  exec gunicorn \
    --workers 3 \
    --worker-class sync \
    --bind "0.0.0.0:${PORT}" \
    --timeout 120 \
    --access-logfile logs/access.log \
    --error-logfile logs/error.log \
    app:app
else
  exec gunicorn \
    --workers 3 \
    --worker-class sync \
    --bind "0.0.0.0:${PORT}" \
    --certfile cert.pem \
    --keyfile key.pem \
    --timeout 120 \
    --access-logfile logs/access.log \
    --error-logfile logs/error.log \
    app:app
fi
