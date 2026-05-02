#!/bin/bash
# =============================================================================
#  deploy-linux.sh  —  Install DB Sync as a Linux background service
#  Works on: Ubuntu, Debian, CentOS, Fedora, Raspberry Pi, any systemd distro
# =============================================================================
#
#  WHAT IT DOES:
#    Registers sync.py as a systemd service that starts automatically on boot
#    and restarts itself if it ever crashes.
#
#  HOW TO RUN:
#    sudo bash deploy-linux.sh --push           # send local data → remote
#    sudo bash deploy-linux.sh --pull           # receive remote data → local
#
#  OPTIONS:
#    --push            local → remote  (use this on the machine that writes data)
#    --pull            remote → local  (use this on the machine that reads data)
#    --watch N         how often to sync in seconds  (default: value in .env)
#    --name  NAME      service name  (default: db-sync)
#    --env   FILE      path to a custom .env file  (default: .env in this folder)
#
#  EXAMPLES:
#    sudo bash deploy-linux.sh --push --watch 30
#    sudo bash deploy-linux.sh --pull --watch 60
#    sudo bash deploy-linux.sh --push --watch 30 --name myapp-sync
#
# =============================================================================

set -e

# ── Where is this project? ────────────────────────────────────────────────────
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$PROJECT_DIR/venv/bin/python"
SCRIPT="$PROJECT_DIR/sync.py"
RUN_USER="${SUDO_USER:-$USER}"

# ── Default values ────────────────────────────────────────────────────────────
SERVICE_NAME="db-sync"
DIRECTION=""
WATCH_VAL=""
ENV_FILE=""

# ── Read options from command line ────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --push)  DIRECTION="--push"; shift ;;
        --pull)  DIRECTION="--pull"; shift ;;
        --watch)
            [[ -n "$2" && "$2" =~ ^[0-9]+$ ]] && { WATCH_VAL="$2"; shift; }
            shift ;;
        --name)  SERVICE_NAME="$2"; shift 2 ;;
        --env)   ENV_FILE="$2";     shift 2 ;;
        *)       echo "Unknown option: $1"; shift ;;
    esac
done

# ── Require --push or --pull ──────────────────────────────────────────────────
if [[ -z "$DIRECTION" ]]; then
    echo ""
    echo "  ERROR: You must choose a direction."
    echo ""
    echo "  Send local data to remote server:"
    echo "    sudo bash deploy-linux.sh --push"
    echo ""
    echo "  Receive remote data to this machine:"
    echo "    sudo bash deploy-linux.sh --pull"
    echo ""
    exit 1
fi

# ── Build the command that will run in the background ─────────────────────────
CMD="$DIRECTION --watch"
[[ -n "$WATCH_VAL" ]] && CMD="$CMD $WATCH_VAL"
[[ -n "$ENV_FILE"  ]] && CMD="$CMD --env $ENV_FILE"

SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"

# ── Pre-flight checks ─────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo "ERROR: Run this script as root:  sudo bash deploy-linux.sh $DIRECTION"
    exit 1
fi

if [[ ! -f "$PYTHON" ]]; then
    echo "ERROR: Python virtual environment not found."
    echo "       Run this first:"
    echo "         python3 -m venv venv"
    echo "         venv/bin/pip install -r requirements.txt"
    exit 1
fi

ENV_CHECK="${ENV_FILE:-$PROJECT_DIR/.env}"
if [[ ! -f "$ENV_CHECK" ]]; then
    echo "ERROR: .env file not found at: $ENV_CHECK"
    echo "       Copy .env.example to .env and fill in your database details."
    exit 1
fi

# ── Create the service file ───────────────────────────────────────────────────
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=DB Sync ($DIRECTION)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$PYTHON $SCRIPT $CMD
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE_NAME

[Install]
WantedBy=multi-user.target
EOF

# ── Enable and start ──────────────────────────────────────────────────────────
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start  "$SERVICE_NAME"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "  ✅  Service '$SERVICE_NAME' is installed and running."
echo ""
echo "  Direction : $DIRECTION"
echo "  Interval  : ${WATCH_VAL:-from .env}"
echo "  Runs as   : $RUN_USER"
echo ""
echo "  ─── Useful commands ───────────────────────────────────────"
echo "  Check status   :  sudo systemctl status  $SERVICE_NAME"
echo "  Stop           :  sudo systemctl stop    $SERVICE_NAME"
echo "  Start          :  sudo systemctl start   $SERVICE_NAME"
echo "  Restart        :  sudo systemctl restart $SERVICE_NAME"
echo "  View live logs :  sudo journalctl -u $SERVICE_NAME -f"
echo ""
echo "  ─── Remove service completely ─────────────────────────────"
echo "  sudo systemctl stop $SERVICE_NAME"
echo "  sudo systemctl disable $SERVICE_NAME"
echo "  sudo rm $SERVICE_FILE"
echo "  sudo systemctl daemon-reload"
echo ""
