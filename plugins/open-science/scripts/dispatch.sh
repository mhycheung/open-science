#!/usr/bin/env bash
# Start an agent in a new window of the current tmux session (open-science:dispatch).
#
#   dispatch.sh launch --dir <dir> [--agent claude|codex] [--name <window name>]
#                      [--prompt-file <file>]
#   dispatch.sh claim <prompt file>      (the mod) print the prompt and remove it, once
#
# launch opens a window (in the background: the user's window stays current) in <dir>,
# types the launch command into its shell, so that the user's shell functions and aliases
# apply, and with --prompt-file hands the new session that prompt:
#   claude  Remote Control on (`--remote-control`). The prompt is copied to the state
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
# the agent (a wrapper function). It prints key=value lines: window, pane, dir, command,
# prompt (none | mod | typed | argument | pending).
set -uo pipefail

STATE="${OPSCI_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/open-science}"
QUEUE="$STATE/dispatch"
GRACE="${OPSCI_DISPATCH_GRACE:-15}"
MAXWAIT="${OPSCI_DISPATCH_MAXWAIT:-180}"

die() { echo "dispatch: $*" >&2; exit 1; }

# Only a launch's own file: a *.prompt file of this user in a `dispatch` directory. (The
# session's state directory may differ from the dispatcher's, so the path is not compared.)
claim() {  # <file>: print it and remove it; exit 1 when already claimed or not ours
  local f="${1:-}"
  [ -n "$f" ] && [ -f "$f" ] && [ ! -L "$f" ] && [ -O "$f" ] || return 1
  [ "$(basename "$(dirname "$f")")" = dispatch ] || return 1
  case "$(basename "$f")" in *.prompt) ;; *) return 1 ;; esac
  local mine="$f.claimed.$$"
  mv "$f" "$mine" 2>/dev/null || return 1
  cat "$mine"
  rm -f "$mine"
}

# The Claude Code prompt box is drawn and nothing runs: a `❯` line, and no busy marker or
# folder-trust question on the screen.
box_ready() {
  local s; s=$(tmux capture-pane -p -t "$1" 2>/dev/null) || return 1
  grep -q '^[[:space:]]*❯' <<<"$s" || return 1
  ! grep -qiE 'esc to interrupt|trust (the files in )?this folder' <<<"$s"
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
  [ -n "$name" ] || name=$(basename "$dir")

  local sess
  sess=$(tmux display-message -p ${TMUX_PANE:+-t "$TMUX_PANE"} '#{session_id}') || die "cannot read the current tmux session"

  local base cmd queued="" envs=()
  if [ "$agent" = claude ]; then
    base="${OPSCI_DISPATCH_CMD:-claude} --remote-control"; cmd=$base
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

  local how=none
  if [ "$agent" = codex ] && [ -n "$pfile" ]; then how=argument; fi
  if [ -n "$queued" ]; then
    how=pending
    local t=0 ready_at=-1
    while [ "$t" -lt "$MAXWAIT" ]; do
      [ -e "$queued" ] || { how=mod; break; }
      if box_ready "$pane"; then
        [ "$ready_at" -ge 0 ] || ready_at=$t
        if [ $((t - ready_at)) -ge "$GRACE" ]; then
          local text
          if text=$(claim "$queued"); then
            tmux set-buffer -b opsci-dispatch -- "$text"
            tmux paste-buffer -p -d -b opsci-dispatch -t "$pane"
            sleep 1
            tmux send-keys -t "$pane" Enter
            how=typed
          else
            how=mod
          fi
          break
        fi
      else
        ready_at=-1
      fi
      sleep 1; t=$((t + 1))
    done
  fi
  printf 'window=%s\npane=%s\ndir=%s\ncommand=%s\nprompt=%s\n' "$win" "$pane" "$dir" "$base" "$how"
  [ "$how" = pending ] && echo "queued=$queued"
  return 0
}

case "${1:-}" in
  launch) shift; launch "$@" ;;
  claim) claim "${2:-}" ;;
  *) echo "usage: dispatch.sh launch --dir <dir> [--agent claude|codex] [--name <name>] [--prompt-file <file>] | claim <file>" >&2; exit 2 ;;
esac
