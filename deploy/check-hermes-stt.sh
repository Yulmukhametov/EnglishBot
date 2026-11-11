#!/usr/bin/env bash
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
hermes_home=${HERMES_HOME:-"$HOME/.hermes"}
python_path=$(cat "$hermes_home/skills/english-lesson-delivery/python-path")
exec "$python_path" "$script_dir/check_hermes_stt.py"
