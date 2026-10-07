#!/usr/bin/env bash
# A minimal imitation of the folder-trust question, run inside a private tmux pane.
#   fake_trust.sh <claude|codex|stuck> <answer log> <command to run on yes...>
# claude: "❯ No, exit" / "Yes, I trust this folder", cursor on No (as Claude Code 2.1.287);
# codex: "› 1. Trust and continue" / "2. Back", cursor on Trust (as codex-cli 0.159.3);
# stuck: like claude, but Down does not move the cursor. Down and Up move the cursor; Enter
# writes the chosen answer (yes | no) to the log, then runs the command on yes, exits on no.
set -u
kind="$1"; log="$2"; shift 2
if [ "$kind" = codex ]; then opts=("1. Trust and continue" "2. Back to Agent Command Center"); yes=0; mark='›'
else opts=("No, exit" "Yes, I trust this folder"); yes=1; mark='❯'; fi
sel=0
draw() {
  printf '\033[H\033[2J'
  if [ "$kind" = codex ]; then printf 'Trust this folder? Codex can read, edit, and run files here.\n'
  else printf 'Quick safety check: Is this a project you created or one you trust?\n'; fi
  local i
  for i in 0 1; do
    if [ "$i" = "$sel" ]; then printf '%s %s\n' "$mark" "${opts[$i]}"; else printf '  %s\n' "${opts[$i]}"; fi
  done
}
while :; do
  draw
  IFS= read -rsn1 k || exit 0
  if [ "$k" = $'\033' ]; then
    read -rsn2 -t 1 rest || rest=""
    [ "$kind" = stuck ] && continue
    case "$rest" in '[B') sel=$(( (sel + 1) % 2 )) ;; '[A') sel=$(( (sel + 1) % 2 )) ;; esac
  elif [ -z "$k" ]; then
    if [ "$sel" = "$yes" ]; then echo yes >> "$log"; exec "$@"; fi
    echo no >> "$log"; exit 0
  fi
done
