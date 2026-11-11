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
# Companion learning workflow, using the same isolated interpreter.
mentor_target="$hermes_home/skills/english-mentor"
mkdir -p "$mentor_target/scripts"
install -m 600 "$repo_dir/hermes-skills/english-mentor/SKILL.md" "$mentor_target/SKILL.md"
install -m 600 "$repo_dir/hermes-skills/english-mentor/scripts/learner.py" "$mentor_target/scripts/learner.py"
# Credential resolver only; no message sending happens when the storage CLI imports it.
install -m 600 "$repo_dir/hermes-skills/english-lesson-delivery/scripts/ordered_delivery.py" "$mentor_target/scripts/ordered_delivery.py"
printf '%s\n' "$python_path" > "$mentor_target/python-path"
cat > "$mentor_target/learner" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
skill_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
interpreter=$(cat -- "$skill_dir/python-path")
exec "$interpreter" "$skill_dir/scripts/learner.py" "$@"
WRAPPER
chmod 700 "$mentor_target/learner"
"$python_path" -c 'import edge_tts, dotenv, imageio_ffmpeg; print("Delivery dependencies OK")'
# Direct delivery must suppress only the extra final reply, scoped to the owner's DM.
# Hermes otherwise replaces a silence token addressed to a person with a visible notice.
if owner_id=$("$python_path" - "$target/scripts" "$hermes_home" <<'PYOWNER'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
try:
    from ordered_delivery import credentials
    _, owner = credentials(Path(sys.argv[2]))
    print(owner)
except Exception:
    raise SystemExit(1) from None
PYOWNER
); then
  "$launcher" config set "platforms.telegram.channel_overrides.$owner_id.allow_silence" true
else
  echo 'Single Telegram owner not configured; finish Telegram setup before enabling per-chat silence.'
fi
echo 'Installed skills: english-lesson-delivery + english-mentor. Restart the gateway and start the initial assessment in Telegram.'
