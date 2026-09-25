# Shared helpers for the open-science context-management scripts. Source, do not run.
#
# Everything that types into a Claude Code pane lives here, so there is one
# implementation of the tmux mechanics. The measured facts it relies on (Claude
# Code 2.1.220 to 2.1.280):
#   * The Stop hook fires about 1 s BEFORE the TUI leaves the busy state. Poll for
#     idle; never type at Stop time.
#   * `/clear` runs only when it is the FIRST thing in the input buffer. Send C-u first.
#   * A literal newline submits, so every typed line must be one line.
#   * Text and Enter are separate send-keys calls, with a pause between them.
#   * The TUI pads its empty prompt line with U+00A0; strip it before comparing.
#   * A message submitted while a turn runs is QUEUED ("Press up to edit queued
#     messages"). Queued counts as submitted.
#   * `<config>/sessions/<pid>.json` holds the live `sessionId` and `status`
#     (`busy`, `idle`, `shell`). The session id changes on /clear, which is how a
#     clear is confirmed.

OS_STATE="${OPSCI_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/open-science}"
OS_LOG="$OS_STATE/cm.log"
OS_PROMPT_RE='❯'
OS_BUSY_RE='esc to interrupt'

cm_log() { mkdir -p "$OS_STATE" 2>/dev/null; echo "[$(date -Iseconds)] $*" >> "$OS_LOG"; }

cm_key() { printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'; }

# Key for this pane: tmux socket + pane id. Pane ids are unique only per server.
cm_pane_key() {  # [sock] [pane] -> key, or return 1 outside tmux
  local t="${TMUX:-}"; local sock="${1:-${t%%,*}}" pane="${2:-${TMUX_PANE:-}}"
  [ -n "${TMUX:-}${1:-}" ] && [ -n "$pane" ] || return 1
  printf '%s__%s' "$(cm_key "$sock")" "$(cm_key "$pane")"
}

cm_request_path() { printf '%s/jump/%s.json' "$OS_STATE" "$1"; }   # <pane key>
cm_reg_path()     { printf '%s/pane_context/%s.json' "$OS_STATE" "$1"; }   # <pane key>; pane_context.sh's record
cm_timer_path()   { printf '%s/timer/%s.pid' "$OS_STATE" "$1"; }   # <pane key>
cm_lock_path()    { mkdir -p "$OS_STATE/lock" 2>/dev/null; printf '%s/lock/%s.lock' "$OS_STATE" "$1"; }

# The claude process that owns this shell: walk up the process tree.
cm_claude_pid() {
  local p="${1:-$$}" c
  while [ -n "$p" ] && [ "$p" -gt 1 ] 2>/dev/null; do
    c=$(ps -o comm= -p "$p" 2>/dev/null)
    [ "$c" = claude ] && { echo "$p"; return 0; }
    p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
  done
  return 1
}

# That process's state file, or return 1.
cm_state_file() {  # <claude pid>
  local d f
  for d in "${CLAUDE_CONFIG_DIR:-}" "$HOME/.claude"; do
    [ -n "$d" ] || continue
    f="$d/sessions/$1.json"
    [ -f "$f" ] && { echo "$f"; return 0; }
  done
  return 1
}

cm_sid()    { jq -r '.sessionId // empty' "$1" 2>/dev/null; }   # <state file>
cm_status() { jq -r '.status // empty' "$1" 2>/dev/null; }      # <state file>

# The live session id of the claude above this shell: its state file, which follows
# /clear, else $CLAUDE_CODE_SESSION_ID. Prints nothing when neither is known.
cm_live_sid() {
  local cpid sf sid=""
  cpid=$(cm_claude_pid) && sf=$(cm_state_file "$cpid") && sid=$(cm_sid "$sf")
  printf '%s' "${sid:-${CLAUDE_CODE_SESSION_ID:-}}"
}

# The session that registered this pane (pane_context.sh set), or nothing. Only that
# session gets the Stop hook's timer and size notice: registration is the opt-in.
cm_registered_sid() {  # <pane key>
  jq -r '.session_id // empty' "$(cm_reg_path "$1")" 2>/dev/null
}

# Hand the pane's registration to the session a jump created. No record, no change.
cm_reg_handover() {  # <pane key> <new sid>
  local f tmp; f=$(cm_reg_path "$1"); tmp="$f.tmp.$$"
  [ -f "$f" ] || return 0
  jq --arg sid "$2" '.session_id=$sid' "$f" > "$tmp" 2>/dev/null && mv "$tmp" "$f" || rm -f "$tmp"
}

# ---- pane reading ------------------------------------------------------------
cm_strip() { sed -e 's/\xc2\xa0//g' -e 's/\xe2\x80\x8b//g' -e 's/\xef\xbb\xbf//g' | tr -d '[:space:]'; }
cm_cap()   { tmux -S "$1" capture-pane -p -t "$2" 2>/dev/null; }          # <sock> <pane>
cm_keys()  { local s="$1" t="$2"; shift 2; tmux -S "$s" send-keys -t "$t" "$@" 2>/dev/null; }

cm_pane_queued() { cm_cap "$1" "$2" | grep -qiE 'queued message|press up to edit queued'; }

cm_input_empty() {  # <sock> <pane>: the live prompt line holds no text
  local last
  last=$(cm_cap "$1" "$2" | grep "$OS_PROMPT_RE" | tail -1)
  [ -n "$last" ] || return 1
  last="${last#*❯}"
  printf '%s' "$last" | grep -qiE 'queued message|press up to edit queued' && return 0
  [ -z "$(printf '%s' "$last" | cm_strip)" ]
}

# Visible text of the input box, from the last prompt glyph to the box border.
cm_input_box() {  # <sock> <pane>
  local cap ln
  cap=$(cm_cap "$1" "$2") || return 1
  ln=$(printf '%s\n' "$cap" | grep -n "$OS_PROMPT_RE" | tail -1 | cut -d: -f1)
  [ -n "$ln" ] || return 1
  printf '%s\n' "$cap" | tail -n +"$ln" | sed '1s/.*❯//' | awk 'index($0,"─"){exit} {print}' | cm_strip
}

cm_clear_input() {  # <sock> <pane>; C-u clears one visual line, BSpace bursts the rest
  local i
  for ((i=0; i<30; i++)); do
    cm_input_empty "$1" "$2" && return 0
    if (( i % 10 == 9 )); then cm_keys "$1" "$2" -N 200 BSpace; sleep 1
    else cm_keys "$1" "$2" C-u; sleep 0.3; fi
  done
  cm_input_empty "$1" "$2"
}

# Idle = the pane shows the prompt, no busy marker, and the state file (when
# readable) does not say busy. A background subagent pins status at "busy" after
# the turn has ended, so a pane that has looked typable for $OPSCI_BG_QUIET polls in a
# row outvotes the status. "shell" with the prompt visible is idle (a background
# shell outlived the turn).
CM_QUIET=0
cm_idle_once() {  # <sock> <pane> <state file or empty>
  local snap st
  snap=$(cm_cap "$1" "$2")
  if printf '%s' "$snap" | grep -q "$OS_BUSY_RE"; then CM_QUIET=0; return 1; fi
  if printf '%s' "$snap" | grep -q "$OS_PROMPT_RE"; then CM_QUIET=$((CM_QUIET+1)); else CM_QUIET=0; return 1; fi
  [ -n "${3:-}" ] || return 0
  st=$(cm_status "$3")
  case "$st" in
    ""|idle|shell) return 0 ;;
  esac
  [ "$CM_QUIET" -ge "${OPSCI_BG_QUIET:-20}" ] && return 0
  return 1
}

# Wait until the pane has been idle for $OPSCI_IDLE_STABLE consecutive 1-s polls.
cm_wait_idle() {  # <sock> <pane> <state file or empty> <timeout s>
  local waited=0 stable=0
  CM_QUIET=0
  while [ "$waited" -lt "$4" ]; do
    if cm_idle_once "$1" "$2" "$3"; then stable=$((stable+1)); else stable=0; fi
    [ "$stable" -ge "${OPSCI_IDLE_STABLE:-5}" ] && return 0
    sleep 1; waited=$((waited+1))
  done
  return 1
}

# Type one line and confirm it was submitted. 0 = submitted, 1 = not.
# Uses the paste buffer (one write) and checks the END of the payload, which the
# input box always keeps on screen even when a long line scrolls its head away.
cm_deliver() {  # <sock> <pane> <text> [attempts]
  local sock="$1" pane="$2" text attempts="${4:-3}" i w box want payload buf="osdeliver$$"
  text=$(printf '%s' "$3" | tr '\n' ' ')
  [ -n "$text" ] || return 1
  want=$(printf '%s' "$text" | rev | cut -c1-24 | rev | cm_strip)
  payload=$(printf '%s' "$text" | cm_strip)
  for ((i=1; i<=attempts; i++)); do
    cm_input_empty "$sock" "$pane" || cm_clear_input "$sock" "$pane" || true
    printf '%s' "$text" | tmux -S "$sock" load-buffer -b "$buf" - 2>/dev/null || continue
    tmux -S "$sock" paste-buffer -d -b "$buf" -t "$pane" 2>/dev/null || continue
    sleep 3
    box=$(cm_input_box "$sock" "$pane")
    if [ -z "$box" ] || [[ "$box" != *"$want"* ]] || [[ "$payload" != *"$box"* ]]; then
      cm_log "deliver: $pane input box does not hold the payload (attempt $i/$attempts)"
      cm_clear_input "$sock" "$pane" || true
      continue
    fi
    cm_keys "$sock" "$pane" Enter
    for ((w=0; w<20; w++)); do
      sleep 1
      if cm_cap "$sock" "$pane" | grep -q "$OS_BUSY_RE" || cm_pane_queued "$sock" "$pane" \
         || cm_input_empty "$sock" "$pane"; then
        cm_log "deliver: submitted to $pane (attempt $i)"
        return 0
      fi
    done
    cm_log "deliver: Enter did not drain $pane's buffer (attempt $i/$attempts)"
  done
  cm_input_empty "$sock" "$pane" || cm_clear_input "$sock" "$pane" || true
  cm_log "deliver: FAILED on $pane after $attempts attempts; nothing submitted"
  return 1
}

# Kill this pane's cache-cold timer, if any.
cm_timer_kill() {  # <pane key>
  local f pid
  f=$(cm_timer_path "$1")
  [ -f "$f" ] || return 0
  pid=$(cat "$f" 2>/dev/null)
  [ -n "$pid" ] && kill -- "-$pid" 2>/dev/null || { [ -n "$pid" ] && kill "$pid" 2>/dev/null; }
  rm -f "$f"
}
