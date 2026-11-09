#!/usr/bin/env bash
# Run only on your Ubuntu 24.04 VPS: sudo bash deploy/install-ubuntu.sh
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then
  echo 'Run with sudo or as root.' >&2
  exit 1
fi
source /etc/os-release
if [[ ${ID:-} != ubuntu || ${VERSION_ID:-} != 24.04 ]]; then
  echo 'This installer supports Ubuntu 24.04 only.' >&2
  exit 1
fi
apt-get update
apt-get install -y ca-certificates git python3 python3-venv ffmpeg
if ! id englishbot >/dev/null 2>&1; then
  useradd --system --user-group --create-home --home-dir /opt/englishbot --shell /usr/sbin/nologin englishbot
fi
if [[ ! -d /opt/englishbot/app ]]; then
  runuser -u englishbot -- git clone https://github.com/Yulmukhametov/EnglishBot.git /opt/englishbot/app
elif [[ ! -f /opt/englishbot/app/pyproject.toml ]]; then
  echo '/opt/englishbot/app exists but is not this application; stopping.' >&2
  exit 1
fi
runuser -u englishbot -- python3 -m venv /opt/englishbot/app/.venv
runuser -u englishbot -- /opt/englishbot/app/.venv/bin/python -m pip install -e /opt/englishbot/app
install -d -m 700 -o englishbot -g englishbot /opt/englishbot/app/data
if [[ ! -e /opt/englishbot/app/.env ]]; then
  install -m 600 -o englishbot -g englishbot /opt/englishbot/app/.env.example /opt/englishbot/app/.env
fi
install -m 644 /opt/englishbot/app/deploy/englishbot.service /etc/systemd/system/englishbot.service
systemctl daemon-reload
echo 'Installed. Fill /opt/englishbot/app/.env on the VPS. Secrets must remain on your server.'
echo 'Then run: systemctl enable --now englishbot'
echo 'Check: systemctl status englishbot --no-pager'
