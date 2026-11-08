#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/telegram-bots/motivation
BOT_USER=tgbot-motivation
BOT_GROUP=tgbot-motivation
SERVICE=telegram-bot@motivation.service
ENV_FILE=/etc/telegram-bots/motivation.env
HELPER=/usr/local/sbin/deploy-motivation-bot
SUDOERS=/etc/sudoers.d/chatgpt-motivation-deployer
REPO_URL=https://github.com/brunarskyy-code/BrunarskyMotivationBot.git

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

id "$BOT_USER" >/dev/null 2>&1 || { echo "Missing user: $BOT_USER" >&2; exit 1; }
test -f "$ENV_FILE" || { echo "Missing $ENV_FILE" >&2; exit 1; }
grep -q '^BOT_TOKEN=' "$ENV_FILE" || { echo "BOT_TOKEN is missing in env file" >&2; exit 1; }

install -d -m 0755 /usr/local/sbin
cat > "$HELPER" <<'HELPER_EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 027

APP_DIR=/opt/telegram-bots/motivation
BOT_USER=tgbot-motivation
BOT_GROUP=tgbot-motivation
SERVICE=telegram-bot@motivation.service
ENV_FILE=/etc/telegram-bots/motivation.env
REPO_URL=https://github.com/brunarskyy-code/BrunarskyMotivationBot.git
LOCK=/run/lock/deploy-motivation-bot.lock

exec 9>"$LOCK"
flock -n 9 || { echo "Another Motivation deployment is already running."; exit 2; }

test -f "$ENV_FILE" || { echo "Missing environment file."; exit 3; }
grep -q '^BOT_TOKEN=' "$ENV_FILE" || { echo "BOT_TOKEN key is missing."; exit 4; }

db_path="$(sed -n 's/^DB_PATH=//p' "$ENV_FILE" | tail -n1 | tr -d '"' | tr -d "'")"
if [[ -z "$db_path" || "$db_path" != /* || "$db_path" == "$APP_DIR"* ]]; then
  echo "Unsafe or missing DB_PATH. Deployment stopped before changing the service."
  exit 5
fi
if [[ ! -f "$db_path" ]]; then
  echo "Database file does not exist at configured DB_PATH. Deployment stopped."
  exit 6
fi

parent="$(dirname "$APP_DIR")"
mkdir -p "$parent"
tmp="$(mktemp -d "$parent/.motivation-new.XXXXXX")"
backup=""
cleanup() {
  [[ -d "$tmp" ]] && rm -rf "$tmp" || true
}
trap cleanup EXIT

chown "$BOT_USER:$BOT_GROUP" "$tmp"
sudo -u "$BOT_USER" git clone --depth 1 --branch main "$REPO_URL" "$tmp/src" >/dev/null

# Reuse the existing virtual environment when available, otherwise create a new one.
if [[ -x "$APP_DIR/.venv/bin/python" ]]; then
  cp -a "$APP_DIR/.venv" "$tmp/src/.venv"
  chown -R "$BOT_USER:$BOT_GROUP" "$tmp/src/.venv"
else
  sudo -u "$BOT_USER" /usr/bin/python3 -m venv "$tmp/src/.venv"
fi

if [[ -f "$tmp/src/requirements.txt" ]]; then
  sudo -u "$BOT_USER" "$tmp/src/.venv/bin/python" -m pip install -q -r "$tmp/src/requirements.txt"
fi
sudo -u "$BOT_USER" "$tmp/src/.venv/bin/python" -m py_compile "$tmp/src/bot.py"

new_sha="$(sudo -u "$BOT_USER" git -C "$tmp/src" rev-parse --short HEAD)"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

systemctl stop "$SERVICE"
if [[ -d "$APP_DIR" ]]; then
  backup="$parent/motivation.prev-$timestamp"
  mv "$APP_DIR" "$backup"
fi

mv "$tmp/src" "$APP_DIR"
chown -R "$BOT_USER:$BOT_GROUP" "$APP_DIR"

if ! systemctl start "$SERVICE"; then
  echo "Start failed; rolling back."
  rm -rf "$APP_DIR"
  if [[ -n "$backup" && -d "$backup" ]]; then
    mv "$backup" "$APP_DIR"
    systemctl start "$SERVICE" || true
  fi
  exit 7
fi

sleep 4
if [[ "$(systemctl is-active "$SERVICE" 2>/dev/null || true)" != "active" ]]; then
  echo "Service is not active after deployment; rolling back."
  systemctl stop "$SERVICE" || true
  rm -rf "$APP_DIR"
  if [[ -n "$backup" && -d "$backup" ]]; then
    mv "$backup" "$APP_DIR"
    systemctl start "$SERVICE" || true
  fi
  exit 8
fi

journal="$(journalctl -u "$SERVICE" -n 60 --no-pager -o cat 2>/dev/null || true)"
if printf '%s\n' "$journal" | grep -Eiq 'Traceback|InvalidToken|Conflict: terminated by other getUpdates|ERROR:'; then
  echo "Service is active but recent log contains an error signature; rolling back."
  systemctl stop "$SERVICE" || true
  rm -rf "$APP_DIR"
  if [[ -n "$backup" && -d "$backup" ]]; then
    mv "$backup" "$APP_DIR"
    systemctl start "$SERVICE" || true
  fi
  exit 9
fi

# Keep only the newest rollback copy.
mapfile -t old_backups < <(ls -1dt "$parent"/motivation.prev-* 2>/dev/null || true)
if (( ${#old_backups[@]} > 1 )); then
  for old in "${old_backups[@]:1}"; do rm -rf "$old"; done
fi

echo "DEPLOY_OK sha=$new_sha service=active"
HELPER_EOF

chmod 0755 "$HELPER"
chown root:root "$HELPER"

cat > "$SUDOERS" <<EOF
ghrunner ALL=(root) NOPASSWD: $HELPER
EOF
chmod 0440 "$SUDOERS"
chown root:root "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null

echo "Bootstrap installed. Running first deployment..."
"$HELPER"
