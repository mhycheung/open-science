#!/usr/bin/env bash
# A minimal imitation of the Claude Code TUI, run inside a private tmux pane.
#   fake_tui.sh <state file> <received log>
# It draws a border and a `❯ ` prompt, appends each submitted line to the log,
# changes `sessionId` in the state file on `/clear`, and shows "esc to interrupt"
# with status "busy" for $FAKE_BUSY seconds after each line (as a turn would).
set -u
sf="$1"; rec="$2"
setkey() { jq --arg v "$2" ".$1=\$v" "$sf" > "$sf.t" && mv "$sf.t" "$sf"; }
while :; do
  printf '\033[H\033[2J'
  printf 'fake claude, session %s\n' "$(jq -r .sessionId "$sf")"
  printf '────────────────────\n'
  setkey status idle
  printf '❯ '
  IFS= read -r line || exit 0
  printf '%s\n' "$line" >> "$rec"
  setkey status busy
  [ "$line" = /clear ] && setkey sessionId "sid-$(date +%s%N)"
  printf '\033[H\033[2J'
  printf 'working (esc to interrupt)\n'
  sleep "${FAKE_BUSY:-1}"
done
