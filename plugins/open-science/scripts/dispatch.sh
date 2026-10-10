#!/usr/bin/env bash
# Start an agent in a new window of the current tmux session (open-science:dispatch).
#
#   dispatch.sh launch --dir <dir> [--agent claude|codex] [--name <name>]
#                      [--prompt-file <file>]
#   dispatch.sh name <dir>               print the name a session dispatched to <dir> gets
#   dispatch.sh trust --pane <pane> [--agent claude|codex] [--queued <file>]
#                                        answer the folder-trust question with yes; only
#                                        after the user said yes
#   dispatch.sh claim <prompt file>      (the mod) print the prompt and remove it, once
#   dispatch.sh peek <prompt file>       (the mod) print the prompt and leave it
#
# launch opens a window (in the background: the user's window stays current) in <dir>,
# types the launch command into its shell, so that the user's shell functions and aliases
# apply, and with --prompt-file hands the new session that prompt. The window and the session
# are named after the project: <project>, or <project>-2, -3, ... (the lowest number free)
# when live sessions of this account already hold the name. <project> is the basename of the
# git top level of <dir> (else of <dir>), lower case, anything but [a-z0-9] turned into '-':
# the names open-science-context gives sessions. A name just given to a dispatched session is
# held for OPSCI_DISPATCH_HOLD seconds (default 120), until that session has recorded it.
# --name gives the name instead.
#   claude  Remote Control on (`--name <name> --remote-control <name>`). The prompt is copied to the state
#           directory and named in OPSCI_DISPATCH_PROMPT, set for the window only; the
#           open-science mod (hooks/dispatch_mod.js) submits it as the user's prompt when
#           the session starts. When no mod claims it, by the time the prompt box has shown
#           for OPSCI_DISPATCH_GRACE seconds (default 15), this script pastes it into the
#           pane and presses Enter. Each prompt file is claimed once (an atomic rename), so
#           the prompt is never sent twice.
#   codex   the prompt is the command's argument (Codex has no mods); Codex has no
#           per-session Remote Control.
# The launch command is $OPSCI_DISPATCH_CMD (claude) or $OPSCI_DISPATCH_CODEX_CMD (codex),
# default `claude` and `codex`: set it when the plain command is not how the user starts
# the agent (a wrapper function). It prints key=value lines: window, pane, dir, name,
# command, prompt (none | mod | typed | argument | pending), trust (asked | none).
# trust=asked: the new session shows its folder-trust question (launch waits up to
# OPSCI_DISPATCH_TRUSTWAIT seconds, default 20, for it or the prompt box). The script never
# answers it on its own: the dispatching agent asks the user, and only on their yes runs
# `trust`, which selects the yes answer and, with --queued, then delivers the prompt as
# launch does.
set -uo pipefail

STATE="${OPSCI_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/open-science}"
QUEUE="$STATE/dispatch"
GRACE="${OPSCI_DISPATCH_GRACE:-15}"
MAXWAIT="${OPSCI_DISPATCH_MAXWAIT:-180}"
TRUSTWAIT="${OPSCI_DISPATCH_TRUSTWAIT:-20}"
HOLD="${OPSCI_DISPATCH_HOLD:-120}"
SDIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/sessions"
SEP=' · '

die() { echo "dispatch: $*" >&2; exit 1; }

# Only a launch's own file: a *.prompt file of this user in a `dispatch` directory. (The
# session's state directory may differ from the dispatcher's, so the path is not compared.)
ours() {
  local f="${1:-}"
  [ -n "$f" ] && [ -f "$f" ] && [ ! -L "$f" ] && [ -O "$f" ] || return 1
  [ "$(basename "$(dirname "$f")")" = dispatch ] || return 1
  case "$(basename "$f")" in *.prompt) ;; *) return 1 ;; esac
}

peek() {  # <file>: print it and leave it; exit 1 when already claimed or not ours
  ours "${1:-}" && cat "$1"
}

claim() {  # <file>: print it and remove it; exit 1 when already claimed or not ours
  local f="${1:-}"
  ours "$f" || return 1
  local mine="$f.claimed.$$"
  mv "$f" "$mine" 2>/dev/null || return 1
  cat "$mine"
  rm -f "$mine"
}

slug() { printf '%s' "$1" | tr 'A-Z' 'a-z' | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//'; }

project_of() {  # <dir>
  local top s
  top=$(git -C "$1" rev-parse --show-toplevel 2>/dev/null) || top="$1"
  s=$(slug "$(basename "$top")")
  printf '%s' "${s:-session}"
}

# Names in use, one per line: the part before SEP of every live Claude Code session's name in
# this config dir (live: its pid runs with the recorded start time), and the names held for
# sessions dispatched in the last HOLD seconds.
taken() {
  local f rec pid ps name now
  for f in "$SDIR"/*.json; do
    [ -f "$f" ] || continue
    rec=$(jq -r '[.pid, .procStart // "", .name // ""] | @tsv' "$f" 2>/dev/null) || continue
    IFS=$'\t' read -r pid ps name <<<"$rec"
    [ -n "$name" ] && [ -r "/proc/$pid/stat" ] || continue
    [ "$(awk '{print $22}' "/proc/$pid/stat" 2>/dev/null)" = "$ps" ] || continue
    printf '%s\n' "${name%%"$SEP"*}"
  done
  now=$(date +%s)
  for f in "$QUEUE"/names/*; do
    [ -f "$f" ] || continue
    if [ $((now - $(stat -c %Y "$f"))) -lt "$HOLD" ]; then basename "$f"; else rm -f "$f"; fi
  done
}

pick() {  # <dir> -> <project>, else <project>-2, -3, ...: the first not taken
  local base cand n=2 used
  base=$(project_of "$1"); cand=$base
  used=$(taken)
  while grep -qxF -- "$cand" <<<"$used"; do cand="$base-$n"; n=$((n + 1)); done
  printf '%s' "$cand"
}

hold() { mkdir -p "$QUEUE/names" 2>/dev/null && : > "$QUEUE/names/$1"; }

# The Claude Code prompt box is drawn and nothing runs: a `❯` line, and no busy marker or
# folder-trust question on the screen.
box_ready() {
  local s; s=$(tmux capture-pane -p -t "$1" 2>/dev/null) || return 1
  grep -q '^[[:space:]]*❯' <<<"$s" || return 1
  ! grep -qiE 'esc to interrupt|trust (the files in )?this folder' <<<"$s"
}

# The folder-trust question is on the screen. Claude Code: "Is this a project you created or
# one you trust?" with "Yes, I trust this folder"; Codex: "Trust this folder?" with "Trust and
# continue" (MEASURED on Claude Code 2.1.287 and codex-cli 0.159.3).
TRUST_Q='trust (the files in )?this folder|trust and continue'
trust_shown() {
  tmux capture-pane -p -t "$1" 2>/dev/null | grep -qiE "$TRUST_Q"
}

# The question's cursor can start on the no answer (Claude Code's starts on "No, exit"), so a
# bare Enter could quit the session. Move it with Down until the yes line is the selected one,
# and only then press Enter. If that never happens, press nothing.
accept_trust() {  # <pane> <agent> -> 0 accepted, 1 could not select yes
  local pane="$1" sel i
  if [ "$2" = codex ]; then sel='(›|❯|>)[[:space:]]*(1\.[[:space:]]*)?Trust and continue'
  else sel='(❯|>)[[:space:]]*(2\.[[:space:]]*)?Yes, I trust'; fi
  for ((i=0; i<4; i++)); do
    if tmux capture-pane -p -t "$pane" 2>/dev/null | grep -Eq "$sel"; then
      tmux send-keys -t "$pane" Enter; return 0
    fi
    tmux send-keys -t "$pane" Down
    sleep "${OPSCI_DISPATCH_KEY_WAIT:-0.5}"
  done
  return 1
}

# Hand a Claude Code session its queued prompt: wait for the mod to claim it, or, once the
# prompt box has been ready for GRACE seconds, paste it. Stops at a folder-trust question.
# Prints mod | typed | pending.
deliver() {  # <pane> <queued file>
  local pane="$1" queued="$2" t=0 ready_at=-1 text
  while [ "$t" -lt "$MAXWAIT" ]; do
    [ -e "$queued" ] || { echo mod; return; }
    trust_shown "$pane" && { echo pending; return; }
    if box_ready "$pane"; then
      [ "$ready_at" -ge 0 ] || ready_at=$t
      if [ $((t - ready_at)) -ge "$GRACE" ]; then
        if text=$(claim "$queued"); then
          tmux set-buffer -b opsci-dispatch -- "$text"
          tmux paste-buffer -p -d -b opsci-dispatch -t "$pane"
          sleep 1
          tmux send-keys -t "$pane" Enter
          echo typed
        else
          echo mod
        fi
        return
      fi
    else
      ready_at=-1
    fi
    sleep 1; t=$((t + 1))
  done
  echo pending
}

# Without a prompt to deliver: wait until the agent shows its prompt box or the folder-trust
# question, up to TRUSTWAIT seconds.
await_first_screen() {  # <pane>
  local t=0 s
  while [ "$t" -lt "$TRUSTWAIT" ]; do
    trust_shown "$1" && return
    s=$(tmux capture-pane -p -t "$1" 2>/dev/null)
    grep -qE '^[[:space:]]*(❯|›)' <<<"$s" && return
    sleep 1; t=$((t + 1))
  done
}

launch() {
  local dir="" agent=claude name="" pfile=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --dir) dir="${2:-}"; shift 2 ;;
      --agent) agent="${2:-}"; shift 2 ;;
      --name) name="${2:-}"; shift 2 ;;
      --prompt-file) pfile="${2:-}"; shift 2 ;;
      *) die "unknown option $1" ;;
    esac
  done
  [ -n "${TMUX:-}" ] || die "not inside tmux: there is no current tmux session to add a window to"
  [ -n "$dir" ] && [ -d "$dir" ] || die "no such directory: ${dir:-(none given)}"
  dir=$(cd "$dir" && pwd -P)
  case "$agent" in claude|codex) ;; *) die "--agent must be claude or codex" ;; esac
  [ -z "$pfile" ] || [ -s "$pfile" ] || die "prompt file missing or empty: $pfile"
  [ -n "$name" ] || name=$(pick "$dir")
  hold "$name"

  local sess
  sess=$(tmux display-message -p ${TMUX_PANE:+-t "$TMUX_PANE"} '#{session_id}') || die "cannot read the current tmux session"

  local base cmd queued="" envs=()
  if [ "$agent" = claude ]; then
    base="${OPSCI_DISPATCH_CMD:-claude} --name $(printf '%q' "$name") --remote-control $(printf '%q' "$name")"; cmd=$base
    if [ -n "$pfile" ]; then
      mkdir -p "$QUEUE" && chmod 700 "$QUEUE" || die "cannot create $QUEUE"
      queued="$QUEUE/$(date +%Y%m%d-%H%M%S)-$$.prompt"
      (umask 077; cp "$pfile" "$queued") || die "cannot write $queued"
      envs=(-e "OPSCI_DISPATCH_PROMPT=$queued")
    fi
  else
    base="${OPSCI_DISPATCH_CODEX_CMD:-codex}"; cmd=$base
    [ -n "$pfile" ] && cmd="$base $(printf '%q' "$(cat "$pfile")")"
  fi

  local pane
  pane=$(tmux new-window -d -P -F '#{pane_id}' -t "$sess:" -n "$name" -c "$dir" "${envs[@]}") \
    || { [ -n "$queued" ] && rm -f "$queued"; die "tmux could not open a window"; }
  tmux send-keys -t "$pane" -l -- "$cmd"
  tmux send-keys -t "$pane" Enter
  local win; win=$(tmux display-message -p -t "$pane" '#{session_name}:#{window_index}')

  local how=none trust=none
  if [ "$agent" = codex ] && [ -n "$pfile" ]; then how=argument; fi
  if [ -n "$queued" ]; then how=$(deliver "$pane" "$queued"); else await_first_screen "$pane"; fi
  trust_shown "$pane" && trust=asked
  printf 'window=%s\npane=%s\ndir=%s\nname=%s\ncommand=%s\nprompt=%s\ntrust=%s\n' \
    "$win" "$pane" "$dir" "$name" "$base" "$how" "$trust"
  [ "$how" = pending ] && echo "queued=$queued"
  return 0
}

trust() {
  local pane="" agent=claude queued=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --pane) pane="${2:-}"; shift 2 ;;
      --agent) agent="${2:-}"; shift 2 ;;
      --queued) queued="${2:-}"; shift 2 ;;
      *) die "unknown option $1" ;;
    esac
  done
  [ -n "$pane" ] || die "--pane is required"
  case "$agent" in claude|codex) ;; *) die "--agent must be claude or codex" ;; esac
  trust_shown "$pane" || die "no folder-trust question in $pane"
  accept_trust "$pane" "$agent" || die "could not select the yes answer in $pane; pressed nothing"
  echo "trust=accepted"
  local t=0
  while trust_shown "$pane" && [ "$t" -lt 10 ]; do sleep 1; t=$((t + 1)); done
  if [ -n "$queued" ]; then
    local how; how=$(deliver "$pane" "$queued")
    echo "prompt=$how"
    [ "$how" = pending ] && echo "queued=$queued"
  fi
  return 0
}

case "${1:-}" in
  launch) shift; launch "$@" ;;
  claim) claim "${2:-}" ;;
  peek) peek "${2:-}" ;;
  trust) shift; trust "$@" ;;
  name) [ -d "${2:-}" ] || die "no such directory: ${2:-(none given)}"; pick "$2"; echo ;;
  *) echo "usage: dispatch.sh launch --dir <dir> [--agent claude|codex] [--name <name>] [--prompt-file <file>] | claim <file> | peek <file> | name <dir> | trust --pane <pane> [--agent claude|codex] [--queued <file>]" >&2; exit 2 ;;
esac
