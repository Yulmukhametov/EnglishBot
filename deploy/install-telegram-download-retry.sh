#!/usr/bin/env bash
set -euo pipefail
if [[ ${EUID} -eq 0 ]]; then
  echo 'Выполните установку под пользователем hermes, не root.' >&2
  exit 1
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
hermes_home=${HERMES_HOME:-"$HOME/.hermes"}
python_path=$(cat "$hermes_home/skills/english-lesson-delivery/python-path")
exec "$python_path" "$script_dir/install_telegram_download_retry.py" "$@"
