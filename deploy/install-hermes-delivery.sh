#!/usr/bin/env bash
set -euo pipefail
if [[ ${EUID} -eq 0 ]]; then
  echo 'Run as the hermes user, not root.' >&2
  exit 1
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(dirname -- "$script_dir")
hermes_home=${HERMES_HOME:-"$HOME/.hermes"}
launcher="$HOME/.local/bin/hermes"
target="$hermes_home/skills/english-lesson-delivery"
if [[ ! -x "$launcher" ]]; then
  echo 'Hermes launcher not found.' >&2
  exit 1
fi
# A separate tool environment; do not mutate the core Hermes runtime.
python_path=$("$launcher" pm environment english-lesson-delivery \
  --requirement 'edge-tts>=7.2,<8' --requirement 'python-dotenv>=1,<2' \
  --requirement 'imageio-ffmpeg>=0.6,<1')
if [[ "$python_path" != /* || ! -x "$python_path" ]]; then
  echo 'PM did not return an executable interpreter path.' >&2
  exit 1
fi
umask 077
mkdir -p "$target/scripts"
install -m 600 "$repo_dir/hermes-skills/english-lesson-delivery/SKILL.md" "$target/SKILL.md"
install -m 600 "$repo_dir/hermes-skills/english-lesson-delivery/scripts/ordered_delivery.py" "$target/scripts/ordered_delivery.py"
printf '%s\n' "$python_path" > "$target/python-path"
cat > "$target/send-lesson" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
skill_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
interpreter=$(cat -- "$skill_dir/python-path")
exec "$interpreter" "$skill_dir/scripts/ordered_delivery.py" "$@"
WRAPPER
chmod 700 "$target/send-lesson"
"$python_path" -c 'import edge_tts, dotenv, imageio_ffmpeg; print("Delivery dependencies OK")'
echo 'Installed skill: english-lesson-delivery. Restart the Hermes gateway, then test the skill in Telegram.'
