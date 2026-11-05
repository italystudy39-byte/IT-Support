#!/usr/bin/env bash
# Установка бота на Linux-сервер (Ubuntu/Debian, Oracle Linux) с автозапуском через systemd.
# Запуск из папки проекта на сервере:  bash deploy/install.sh
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"

if command -v apt-get >/dev/null; then
  sudo apt-get update -y && sudo apt-get install -y python3 python3-venv python3-pip
elif command -v dnf >/dev/null; then
  sudo dnf install -y python3 python3-pip
fi

cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
[ -f .env ] || { cp .env.example .env; echo "!!! Заполните $APP_DIR/.env (TELEGRAM_BOT_TOKEN, GEMINI_API_KEY) и запустите скрипт снова"; exit 1; }

sudo tee /etc/systemd/system/helpdesk-bot.service >/dev/null <<UNIT
[Unit]
Description=AI IT-Helpdesk Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python -m helpdesk.channels.telegram_bot
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now helpdesk-bot
sleep 3
systemctl --no-pager status helpdesk-bot | head -5
echo "Готово. Логи: journalctl -u helpdesk-bot -f   Перезапуск: sudo systemctl restart helpdesk-bot"
