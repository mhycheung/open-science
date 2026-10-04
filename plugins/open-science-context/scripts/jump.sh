#!/usr/bin/env bash
# Request a session jump: clear this Claude session and (for an active jump) resume
# it from a context file.
#
#   jump.sh active <context file> --report "<text>" [--force]
#                                            clear, then type
#                                            "/open-science-context:continue-context <context file>"
#   jump.sh wait   <context file> --report "<text>"
#                                            clear only; a running subagent, background
#                                            shell or SLURM watcher wakes the new session
#   jump.sh cancel                           drop this pane's pending request
#   jump.sh status                           show it
#
# This script only VALIDATES, REPORTS and WRITES a request file.
#
# The report: a jump clears the conversation, so all the user sees of it is "/clear". Every
# jump therefore carries a report for the user (what was done, the state, what runs and
# what comes next), sent with `opsci notify --kind status --no-mention` (the project's
# Feed in Notion, or its configured back end) once every check has passed, before the
# request is written. A failed send does not stop the jump; it is logged and printed
# (`opsci notify` keeps the message in messages/).
#
# With the Claude Code mod loaded (OPSCI_MOD=1, cm_mod_active) the request is keyed by
# session id and tmux is not needed: at the stop the mod runs `cm_stop.sh --mod`, which
# hands the request over, and the mod clears the session (Claude Code's own /clear,
# run from inside), hands the registration to the new session (cm_mod.sh handover) and
# runs the resume prompt. Everything below about panes and typing is the fallback when the
# mod is not loaded.
#
# Without the mod, the plugin's Stop hook
# (cm_stop.sh) reads it when the turn ends: for a wait jump it refuses when
# nothing will wake the session; otherwise it starts the detached worker
# (`jump.sh --worker <request>`) that waits for the pane to go idle, types /clear,
# confirms the session id changed, hands the pane registration (pane_context.sh) to
# the new session, and types the resume prompt. The agent never
# types into its own pane, and a jump is detected by this action, never by wording.
#
# Under Codex (cm_runtime) there is no /clear: a jump ENDS the Codex TUI in the pane and
# starts a fresh `codex` in the pane's shell, with the same options (cm_codex_relaunch_cmd)
# and the resume prompt as its first message (cm_codex_prompt). The Codex hook
# (cx_hook.sh) starts the worker at Stop and hands the registration over at the new
# SessionStart. A Codex wait jump relaunches the same way (the new session reads the
# context file and ends its turn); its waker is `wait_slurm.sh --notify`, which wakes the
# pane's current thread with `codex queue`. Codex needs, in addition: the hook installed
# (a record of this thread in this pane), and Codex started from a shell in the pane.
#
# Refusals (exit 1): no --report text; jumps switched off by $OPSCI_JUMPS (`off`: every jump; `wait`:
# active jumps); not in tmux (without the mod); context file missing; context file not modified
# in the last $OPSCI_JUMP_FRESH_MIN minutes (default 15: the state was not saved);
# the session's state file cannot be found (a clear could not be confirmed; without the mod);
# active jump below $OPSCI_ACTIVE_JUMP_FLOOR tokens (default 100000) without
# --force; an inhibit file exists ($OPSCI_STATE_DIR/inhibit_jump, or
# inhibit_jump_<pane key>; an optional component such as SLURM resurrection may
# create one while it owns the pane).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cm_lib.sh
. "$HERE/cm_lib.sh"

FRESH_MIN="${OPSCI_JUMP_FRESH_MIN:-15}"
FLOOR="${OPSCI_ACTIVE_JUMP_FLOOR:-100000}"
IDLE_TIMEOUT="${OPSCI_JUMP_IDLE_TIMEOUT:-300}"
RECOVER_WINDOW="${OPSCI_JUMP_RECOVER_WINDOW:-1800}"

die() { echo "jump.sh: $*" >&2; exit 1; }

set_phase() {  # <request> <phase>; returns 1 if the request could not be updated
  local tmp="$1.tmp.$$"
  jq --arg p "$2" --arg at "$(date -Iseconds)" '.phase=$p | .phase_at=$at' "$1" > "$tmp" 2>/dev/null \
    && mv "$tmp" "$1" || { rm -f "$tmp"; return 1; }
}

# ------------------------------------------------------------ codex worker ----
# Wait until the thread is idle (the hooks' record says idle and the pane shows no busy
# marker, for $OPSCI_IDLE_STABLE polls in a row), end the TUI with SIGTERM, check the
# pane is back at its shell, type the relaunch command into the SHELL, and wait for the
# new thread's SessionStart (cx_hook.sh finishes the request). Never types into Codex.
cx_fail() {  # <request> <message>: log, tell the user what state the pane is in, give up
  local req="$1" msg="$2" tid home phase full
  tid=$(jq -r .old_sid "$req"); phase=$(jq -r '.phase' "$req")
  # Say exactly what was done, by phase: before `ended` the old session was not touched.
  case "$phase" in
    ended|relaunching)
      full="[open-science] session jump FAILED after the old Codex session (thread $tid) was ENDED: $msg. No new session was started by the jump. Resume the old one with: codex resume $tid" ;;
    terminating)
      full="[open-science] session jump FAILED after SIGTERM was sent to the old Codex TUI (thread $tid), which did not exit: $msg. It may still be shutting down; if it is gone, resume it with: codex resume $tid" ;;
    *)
      full="[open-science] session jump FAILED before anything was changed; this session keeps running: $msg" ;;
  esac
  cm_log "codex worker $(jq -r .pane "$req"): $full"
  jq --arg m "$full" '.failure=$m' "$req" > "$req.tmp" 2>/dev/null && mv "$req.tmp" "$req"
  # Queued to the old thread: it starts a turn if the session still runs, and is waiting
  # in the thread if it was ended (seen on `codex resume`).
  if command -v codex >/dev/null 2>&1; then
    home=$(jq -r '.codex_home // ""' "$(cm_cx_thread_path "$tid")" 2>/dev/null)
    ${home:+env CODEX_HOME="$home"} timeout 60 codex queue --thread "$tid" --message "$full" >/dev/null 2>&1 || true
  fi
  set_phase "$req" failed
  mkdir -p "$OS_STATE/jump/done"; mv "$req" "$OS_STATE/jump/done/$(basename "$req" .json)-$(date +%s).json" 2>/dev/null
}
codex_worker() {
  local req="$1" sock pane old tf tpid tstart ppid cmd stable=0 waited=0 st shell model n cur
  sock=$(jq -r .sock "$req"); pane=$(jq -r .pane "$req"); old=$(jq -r .old_sid "$req")
  tf=$(cm_cx_thread_path "$old")
  cm_log "codex worker $pane: $(jq -r .kind "$req") jump started (thread ${old:0:8})"
  while [ "$waited" -lt "$IDLE_TIMEOUT" ]; do
    st=$(jq -r '.status // ""' "$tf" 2>/dev/null)
    if [ "$st" = idle ] && ! cm_cap "$sock" "$pane" | grep -q "$OS_BUSY_RE"; then stable=$((stable+1)); else stable=0; fi
    [ "$stable" -ge "${OPSCI_IDLE_STABLE:-5}" ] && break
    sleep 1; waited=$((waited+1))
  done
  [ "$stable" -ge "${OPSCI_IDLE_STABLE:-5}" ] || { cx_fail "$req" "the session was not idle for ${IDLE_TIMEOUT} s"; return; }
  tpid=$(jq -r '.tui_pid // ""' "$tf"); tstart=$(jq -r '.tui_start // ""' "$tf")
  cm_is_codex_proc "$tpid" "$tstart" || { cx_fail "$req" "the Codex TUI (pid ${tpid:-unknown}) is not running"; return; }
  ppid=$(tmux -S "$sock" display-message -p -t "$pane" '#{pane_pid}' 2>/dev/null)
  shell=$(ps -o comm= -p "$ppid" 2>/dev/null)
  case "$shell" in bash|zsh|sh|dash|ksh|fish|tcsh|csh) ;; *)
    cx_fail "$req" "pane $pane does not run a shell (it runs '${shell:-nothing}'), so a new codex cannot be started in it; start Codex from a shell in the pane"; return ;; esac
  cm_descends "$tpid" "$ppid" || { cx_fail "$req" "the Codex TUI is not running in pane $pane"; return; }
  model=$(jq -r '.model // ""' "$tf")
  cmd=$(cm_codex_relaunch_cmd "$tpid" "$(jq -r .prompt "$req")" "$model" 2>&1) \
    || { cx_fail "$req" "cannot rebuild the codex command line: $cmd"; return; }
  cmd="cd $(printf '%q' "$(readlink "/proc/$tpid/cwd")") && $cmd"
  jq --arg c "$cmd" '.relaunch=$c' "$req" > "$req.tmp" && mv "$req.tmp" "$req"
  set_phase "$req" terminating || { cx_fail "$req" "could not update the jump request $req"; return; }
  kill -TERM "$tpid" 2>/dev/null
  for n in $(seq 30); do cm_is_codex_proc "$tpid" "$tstart" || break; sleep 1; done
  if cm_is_codex_proc "$tpid" "$tstart"; then
    cx_fail "$req" "the Codex TUI (pid $tpid) is still running 30 s after SIGTERM; nothing else was done"; return
  fi
  set_phase "$req" ended
  # Type only into the pane's own shell: if anything else holds the foreground (the TUI
  # left a child behind, the shell started something), typing would feed that program.
  for n in $(seq "${OPSCI_CODEX_SHELL_TIMEOUT:-20}"); do
    cur=$(tmux -S "$sock" display-message -p -t "$pane" '#{pane_current_command}' 2>/dev/null)
    [ "$cur" = "$shell" ] && break
    sleep 1
  done
  if [ "$cur" != "$shell" ]; then
    cx_fail "$req" "pane $pane did not return to its shell '$shell' (foreground: '${cur:-none}'); nothing was typed. Start Codex there by hand: $cmd"; return
  fi
  sleep 1
  # The shell's line editor, not the Codex TUI: a single line and Enter are reliable here.
  tmux -S "$sock" send-keys -t "$pane" C-u 2>/dev/null; sleep 0.3
  tmux -S "$sock" send-keys -t "$pane" -l "$cmd" 2>/dev/null; sleep 0.5
  # The phase must say `relaunched` BEFORE Enter: the new session's SessionStart hook
  # hands the registration over only for a request in that phase.
  if ! set_phase "$req" relaunched; then
    tmux -S "$sock" send-keys -t "$pane" C-u 2>/dev/null
    cx_fail "$req" "could not update the jump request $req; nothing was started. Start Codex there by hand: $cmd"; return
  fi
  tmux -S "$sock" send-keys -t "$pane" Enter 2>/dev/null
  cm_log "codex worker $pane: old TUI ended; relaunched: $cmd"
  for n in $(seq "${OPSCI_CODEX_START_TIMEOUT:-120}"); do
    [ -f "$req" ] || { cm_log "codex worker $pane: new thread confirmed"; return; }
    sleep 1
  done
  cm_log "codex worker $pane: NO SessionStart from a new thread within ${OPSCI_CODEX_START_TIMEOUT:-120} s; the command was typed into the shell but the new session is unconfirmed (is cx_hook.sh installed and trusted?)"
  set_phase "$req" unconfirmed
  mkdir -p "$OS_STATE/jump/done"; mv "$req" "$OS_STATE/jump/done/$(basename "$req" .json)-$(date +%s).json" 2>/dev/null
}

# ------------------------------------------------------------------ worker ----
if [ "${1:-}" = "--worker" ]; then
  REQ="$2"
  [ -f "$REQ" ] || exit 0
  KIND=$(jq -r .kind "$REQ"); SOCK=$(jq -r .sock "$REQ"); PANE=$(jq -r .pane "$REQ")
  SF=$(jq -r .state_file "$REQ"); OLD=$(jq -r .old_sid "$REQ"); PROMPT=$(jq -r '.prompt // ""' "$REQ")
  KEY=$(jq -r .key "$REQ")
  exec 9>"$(cm_lock_path "$KEY")"
  flock -w 120 9 || { cm_log "worker $PANE: pane lock busy for 120 s; jump aborted"; set_phase "$REQ" failed; exit 0; }
  set_phase "$REQ" running
  if [ "$(jq -r '.runtime // "claude"' "$REQ")" = codex ]; then codex_worker "$REQ"; exit 0; fi
  cm_log "worker $PANE: $KIND jump started (sid ${OLD:0:8})"

  finish() { set_phase "$REQ" "$1"; mkdir -p "$OS_STATE/jump/done"; mv "$REQ" "$OS_STATE/jump/done/$(basename "$REQ" .json)-$(date +%s).json" 2>/dev/null; }

  skip_clear=0
  live=$(cm_sid "$SF")
  if [ -n "$live" ] && [ "$live" != "$OLD" ]; then
    # Someone else already cleared this pane since the request was made.
    [ "$KIND" = wait ] && { cm_log "worker $PANE: already on new sid ${live:0:8}; nothing to do"; cm_reg_handover "$KEY" "$live"; finish done; exit 0; }
    cm_log "worker $PANE: already on new sid ${live:0:8}; skipping the clear"; skip_clear=1
    cm_reg_handover "$KEY" "$live"
  fi

  if [ "$skip_clear" = 0 ]; then
    submitted=0; start=$(date +%s)
    while [ $(( $(date +%s) - start )) -lt "$IDLE_TIMEOUT" ]; do
      cm_wait_idle "$SOCK" "$PANE" "$SF" $(( IDLE_TIMEOUT - ($(date +%s) - start) )) || break
      cm_keys "$SOCK" "$PANE" C-u; sleep 1
      cm_idle_once "$SOCK" "$PANE" "$SF" || { cm_keys "$SOCK" "$PANE" C-u; continue; }
      cm_keys "$SOCK" "$PANE" '/clear'; sleep 2
      # Enter pressed while a turn runs QUEUES the /clear; it would fire later, mid-work.
      cm_idle_once "$SOCK" "$PANE" "$SF" || { cm_log "worker $PANE: went busy after typing /clear; re-waiting"; cm_keys "$SOCK" "$PANE" C-u; continue; }
      [ "$(cm_input_box "$SOCK" "$PANE")" = "/clear" ] || { cm_log "worker $PANE: buffer does not hold exactly /clear; re-waiting"; cm_clear_input "$SOCK" "$PANE"; continue; }
      cm_keys "$SOCK" "$PANE" Enter; submitted=1; break
    done
    if [ "$submitted" = 0 ]; then
      cm_log "worker $PANE: pane never idle for ${IDLE_TIMEOUT} s; nothing typed"
      cm_keys "$SOCK" "$PANE" C-u; finish failed; exit 0
    fi
    new=""; for _ in $(seq 30); do new=$(cm_sid "$SF"); [ -n "$new" ] && [ "$new" != "$OLD" ] && break; sleep 1; done
    if [ -z "$new" ] || [ "$new" = "$OLD" ]; then
      # Fail safe: never type the resume prompt into a session that was not cleared.
      # The TUI may have queued the /clear; if the session id changes later, that is
      # it firing, and the resume prompt is delivered then.
      cm_log "worker $PANE: CLEAR NOT CONFIRMED; watching ${RECOVER_WINDOW} s for a queued clear"
      set_phase "$REQ" unconfirmed
      waited=0
      while [ "$waited" -lt "$RECOVER_WINDOW" ]; do
        new=$(cm_sid "$SF"); [ -n "$new" ] && [ "$new" != "$OLD" ] && break
        sleep 5; waited=$((waited+5))
      done
      if [ -z "$new" ] || [ "$new" = "$OLD" ]; then cm_log "worker $PANE: no clear happened; jump failed"; finish failed; exit 0; fi
      cm_log "worker $PANE: queued clear fired after ${waited} s"
    fi
    cm_log "worker $PANE: cleared, new sid ${new:0:8}"
    cm_reg_handover "$KEY" "$new"
    set_phase "$REQ" cleared
  fi

  if [ "$KIND" = wait ]; then cm_log "worker $PANE: wait jump done; a waker owns the resume"; finish done; exit 0; fi
  sleep 3
  if cm_deliver "$SOCK" "$PANE" "$PROMPT"; then finish done
  else cm_log "worker $PANE: resume prompt NOT delivered; type it by hand: $PROMPT"; finish failed; fi
  exit 0
fi

# ---------------------------------------------------------------- launcher ----
usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }
CMD="${1:-}"; shift || true
MOD=0
if cm_mod_active; then
  MOD=1; OLD=$(cm_live_sid)
  [ -n "$OLD" ] || die "no Claude session id found (no state file, CLAUDE_CODE_SESSION_ID unset)"
  KEY=$(cm_sess_key "$OLD"); PKEY=$(cm_pane_key) || PKEY=""
else
  KEY=$(cm_pane_key) || { [ "$CMD" = status ] && { echo "not in tmux, and the open-science mod is not loaded"; exit 0; }; die "not inside tmux, and the open-science mod is not loaded (it needs a recent Claude Code: run 'claude update'). Without the mod, jumps need tmux."; }
  PKEY="$KEY"
fi
REQ=$(cm_request_path "$KEY")

case "$CMD" in
  status)
    echo "jumps allowed: $(cm_jump_mode) (OPSCI_JUMPS)"
    if [ "$MOD" = 1 ]; then echo "done by: the open-science mod (session ${OLD:0:8})"; else echo "done by: tmux typing (the open-science mod is not loaded)"; fi
    if [ -f "$REQ" ]; then jq . "$REQ"; else echo "no jump pending"; fi; exit 0 ;;
  cancel)
    if [ -f "$REQ" ] && [ "$(jq -r .phase "$REQ")" = requested ]; then rm -f "$REQ"; echo "jump request cancelled"
    elif [ -f "$REQ" ]; then echo "a jump is already $(jq -r .phase "$REQ"); it cannot be cancelled" >&2; exit 1
    else echo "no jump pending"; fi
    exit 0 ;;
  active|wait) ;;
  *) usage ;;
esac
case "$(cm_jump_mode)" in
  off) die "session jumps are switched off (OPSCI_JUMPS=off). Keep the context file current and carry on in this session." ;;
  wait) [ "$CMD" = active ] && die "active jumps are switched off (OPSCI_JUMPS=wait); only wait jumps are allowed. Carry on in this session." ;;
esac

CTX="${1:-}"; [ -n "$CTX" ] || usage; shift
FORCE=0; REPORT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --force) FORCE=1 ;;
    --report) [ $# -ge 2 ] || usage; REPORT="$2"; shift ;;
    *) usage ;;
  esac
  shift
done
[ -n "${REPORT//[[:space:]]/}" ] || die "no report: pass --report with a headline, a blank line, then what was done, the state, what runs and what comes next. A jump clears the conversation, so the user sees only /clear; the report is what tells them what happened."
[ -f "$CTX" ] || die "context file not found: $CTX"
CTX=$(cd "$(dirname "$CTX")" && printf '%s/%s' "$PWD" "$(basename "$CTX")")
case "$CTX" in *[[:space:]]*) die "context file path contains whitespace: $CTX" ;; esac
# The context plugin needs the project management component (plugin open-science-project): the
# context file must sit in a project with AGENTS.md and config/framework.yaml.
d=$(dirname "$CTX")
while [ ! -f "$d/AGENTS.md" ] || [ ! -f "$d/config/framework.yaml" ]; do
  [ "$d" = / ] && die "$CTX is not inside a project made from the open-science template (no AGENTS.md and config/framework.yaml above it). Session jumps need the project management component: create the project with open-science-project:new-project or migrate it with open-science-project:migrate-project."
  d=$(dirname "$d")
done
for f in "$OS_STATE/inhibit_jump" "$OS_STATE/inhibit_jump_$KEY" ${PKEY:+"$OS_STATE/inhibit_jump_$PKEY"}; do
  [ -e "$f" ] && die "jumps are inhibited by $f: $(head -c 300 "$f" 2>/dev/null)"
done
ROOT="$d"

# Send the report to the user (see the header). Called once every check has passed.
# The same text goes into the request: with the mod, it is written at the top of the cleared
# session (seen by the user, read by the agent at its next turn; it starts no turn).
MSG=$(printf '%s\n\n%s jump: the session is cleared and %s from %s.' "$REPORT" "${CMD^}" \
  "$([ "$CMD" = active ] && echo resumes || echo "waits for its background work, then resumes")" "${CTX#"$ROOT"/}")
send_report() {
  local msg="$MSG" out rc
  if ! command -v opsci >/dev/null 2>&1; then
    cm_log "jump report not sent (opsci not on PATH): ${REPORT%%$'\n'*}"
    echo "WARNING: opsci is not on PATH, so the jump report was not sent to the user."
    return 0
  fi
  out=$(opsci notify --project-root "$ROOT" --kind status --no-mention "$msg" 2>&1); rc=$?
  if [ "$rc" = 0 ]; then echo "Report sent: $out"
  else
    cm_log "jump report FAILED (opsci notify exit $rc): ${REPORT%%$'\n'*}"
    echo "WARNING: the jump report was not delivered (opsci notify exit $rc): $out"
  fi
}

age_min=$(( ( $(date +%s) - $(stat -c %Y "$CTX") ) / 60 ))
[ "$age_min" -lt "$FRESH_MIN" ] || die "$CTX was last modified ${age_min} min ago. Save the state first (summary, next step, log line), then jump."
if [ "$(cm_runtime)" = codex ]; then
  cm_state_writable || { cm_state_hint >&2; exit 3; }
  OLD="${CODEX_THREAD_ID:-}"; [ -n "$OLD" ] || die "CODEX_THREAD_ID is not set; cannot tell which Codex thread to end"
  PREC=$(cm_cx_pane_path "$KEY")
  [ "$(jq -r '.thread_id // ""' "$PREC" 2>/dev/null)" = "$OLD" ] \
    || die "no Codex hook record of this thread in this pane ($PREC). The open-science Codex hook (cx_hook.sh) must be installed and trusted for jumps."
  TP=$(jq -r '.transcript_path // ""' "$PREC")
  if [ "$CMD" = active ] && [ "$FORCE" = 0 ]; then
    tokens=$(python3 "$HERE/ctx_usage.py" --codex-rollout "$TP" 2>/dev/null) || tokens=""
    [ -n "$tokens" ] || die "cannot read the context size (the Codex rollout format is not stable); pass --force to jump anyway"
    CFLOOR="${OPSCI_CODEX_ACTIVE_JUMP_FLOOR:-$FLOOR}"
    [ "$tokens" -ge "$CFLOOR" ] || die "context is ${tokens} tokens, below the active-jump floor ${CFLOOR}: a jump costs more than it saves. Continue here, or pass --force."
  fi
  PROMPT=$(cm_codex_prompt "$CTX")
  [ -f "$REQ" ] && [ "$(jq -r .phase "$REQ")" != requested ] && die "a jump is already $(jq -r .phase "$REQ") for this pane"
  send_report
  mkdir -p "$(dirname "$REQ")" 2>/dev/null || die "cannot write to $OS_STATE; in the Codex sandbox, add it as a writable root"
  jq -n --arg kind "$CMD" --arg ctx "$CTX" --arg prompt "$PROMPT" --arg sock "${TMUX%%,*}" \
        --arg pane "$TMUX_PANE" --arg key "$KEY" --arg sid "$OLD" --arg at "$(date -Iseconds)" \
    '{version:1, runtime:"codex", kind:$kind, context:$ctx, prompt:$prompt, sock:$sock, pane:$pane,
      key:$key, state_file:"", old_sid:$sid, requested_at:$at, phase:"requested"}' > "$REQ.tmp" \
    && mv "$REQ.tmp" "$REQ" || die "could not write $REQ (in the Codex sandbox, add $OS_STATE as a writable root)"
  cm_log "launcher $TMUX_PANE: codex $CMD jump requested (context $CTX)"
  bash "$HERE/pane_context.sh" set "$CTX" >/dev/null 2>&1 || true
  echo "Codex $CMD jump requested. When this turn ends, this Codex session is ENDED and a fresh one"
  echo "starts in this pane with the same options and the prompt:"
  echo "  $PROMPT"
  [ "$CMD" = wait ] && echo "The Stop hook refuses the jump unless a waker (wait_slurm.sh --notify) is queued or running."
  echo "Finish anything you owe the user in this turn, then end the turn. Start no new work."
  exit 0
fi
if [ "$MOD" = 0 ]; then
  CPID=$(cm_claude_pid) || die "no claude process above this shell; cannot confirm a clear"
  SF=$(cm_state_file "$CPID") || die "no state file for claude pid $CPID; cannot confirm a clear, refusing"
  OLD=$(cm_sid "$SF"); [ -n "$OLD" ] || die "state file $SF has no sessionId"
fi

if [ "$CMD" = active ]; then
  tokens=$(python3 "$HERE/ctx_usage.py" --session-id "$OLD" 2>/dev/null) || tokens=""
  if [ "$FORCE" = 0 ]; then
    [ -n "$tokens" ] || die "cannot read the context size; pass --force to jump anyway"
    [ "$tokens" -ge "$FLOOR" ] || die "context is ${tokens} tokens, below the active-jump floor ${FLOOR}: a jump costs more than it saves. Continue here, or pass --force."
  fi
  PROMPT="/open-science-context:continue-context $CTX"
else
  PROMPT=""
fi

[ -f "$REQ" ] && [ "$(jq -r .phase "$REQ")" != requested ] && die "a jump is already $(jq -r .phase "$REQ") for this pane"
send_report
mkdir -p "$(dirname "$REQ")"
T="${TMUX:-}"
jq -n --arg kind "$CMD" --arg ctx "$CTX" --arg prompt "$PROMPT" --arg sock "${T%%,*}" \
      --arg pane "${TMUX_PANE:-}" --arg key "$KEY" --arg sf "${SF:-}" --arg sid "$OLD" --arg at "$(date -Iseconds)" \
      --arg rt "$([ "$MOD" = 1 ] && echo claude-mod || echo claude)" --arg report "$MSG" \
  '{version:1, runtime:$rt, kind:$kind, context:$ctx, prompt:$prompt, report:$report, sock:$sock, pane:$pane,
    key:$key, state_file:$sf, old_sid:$sid, requested_at:$at, phase:"requested"}' > "$REQ.tmp" && mv "$REQ.tmp" "$REQ" \
  || die "could not write $REQ"
cm_log "launcher ${TMUX_PANE:-session ${OLD:0:8}}: $CMD jump requested (context $CTX)"
# Register the pane, so a session woken after a wait jump finds its file with
# open-science-context:continue-context and no file named.
bash "$HERE/pane_context.sh" set "$CTX" >/dev/null 2>&1 || true

if [ "$CMD" = active ]; then
  echo "Active jump requested. When this turn ends the session is cleared and resumed with:"
  echo "  $PROMPT"
else
  echo "Wait jump requested. When this turn ends the Stop hook checks that something will wake"
  echo "the new session (a background subagent or shell, or a queued waker). If nothing will, it refuses and tells you."
fi
echo "Finish anything you owe the user in this turn, then end the turn. Start no new work."
