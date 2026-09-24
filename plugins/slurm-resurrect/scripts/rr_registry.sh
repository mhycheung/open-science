#!/bin/bash
# Registry and control commands for slurm-resurrect.
#
# User-only (refused when run by an agent; see rr_require_user in rr_common.sh):
#   register [--remote-control on|off] [--permission-mode MODE] [session ...]
#                                  opt the current (or named) tmux session(s) in.
#                                  The WHOLE tmux session is resurrected: every
#                                  window/pane/layout, and any Claude running in
#                                  a pane. The first run only shows a warning.
#   reset [N]                      count->0, stop->false, optionally max->N
#   set <key> <value>              change a lineage setting (see `set` alone)
#   set-notify '<cmd>'             run <cmd> with $RR_MSG on each notice
# Open to anyone, agents included:
#   remove [session]               opt a tmux session out
#   stop                           disable the lineage; scancel the pending successor
#   note "..."                     self-message for THIS pane, delivered after resurrection
#   timeleft                       runway; past the wind-down threshold?
#   status | list [job] | count [job] | help
#
# State lives in $RR_STATE_DIR (default: the XDG state dir, slurm-resurrect/).
# `register` records the session name + its tmux socket in
# $RR_STATE_DIR/registry/<job>/<sanitized>.json, together with the user's
# permission-mode and Remote Control choices, and captures a fallback snapshot
# under registry/<job>/snapshot/. The coordinator re-snapshots periodically and
# at the pause threshold; the successor job rebuilds every session from that.
set -u
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/rr_common.sh"

sanitize() { printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'; }

# --- prune registry dirs for jobs no longer in the queue ---------------------
prune_stale() {
  local raw live d jid
  raw=$(squeue -h -u "${USER:-$(id -un)}" -o '%i' 2>/dev/null)
  [[ -z "${raw//[$'\n\t ']/}" ]] && { rr_log "squeue empty; skip prune"; return 0; }
  live=" $(echo "$raw" | tr '\n' ' ') "
  for d in "$RR_REG_ROOT"/*/; do
    [[ -d "$d" ]] || continue
    jid=$(basename "$d")
    [[ "$jid" =~ ^[0-9]+$ ]] || continue
    [[ "$jid" == "${SLURM_JOB_ID:-}" ]] && continue
    [[ "$live" != *" $jid "* ]] && { rr_log "prune stale job $jid"; rm -rf "$d"; }
  done
}

# --- elect a single coordinator for this job via flock -----------------------
ensure_coordinator() {
  local job="$1" fd
  local lock="$RR_LOCK_DIR/coordinator_${job}.lock"
  exec {fd}>"$lock"
  if flock -n "$fd"; then
    flock -u "$fd"; exec {fd}>&-
    if [[ ! -f "$RR_LOCK_DIR/coordinator_${job}.running" ]]; then
      rr_log "electing coordinator for job $job"
      setsid nohup bash "$RR_STABLE_SCRIPTS/rr_coordinator.sh" "$job" \
        >> "$RR_HOME/coordinator_${job}.log" 2>&1 < /dev/null &
    fi
  else
    exec {fd}>&-; rr_log "coordinator already running for job $job"
  fi
}

# The tmux session holding the caller's pane. TMUX_PANE is exact; a bare
# `display-message -p` would name the most recently used client's session.
current_session() {
  local sock="$1"
  if [[ -n "${TMUX_PANE:-}" ]]; then
    tmux -S "$sock" display-message -p -t "$TMUX_PANE" '#S' 2>/dev/null
  else
    tmux -S "$sock" display-message -p '#S' 2>/dev/null
  fi
}

RR_PERM_MODES="acceptEdits auto bypassPermissions dontAsk manual plan"  # choices of claude --permission-mode (2.1.280)

first_use_warning() {
  cat <<'WARN'
slurm-resurrect: read this once before you register a session.

A registered tmux session is rebuilt in a new SLURM job when this job reaches
its time limit, and every Claude pane in it is resumed with no one watching.

  * Permission mode (default: bypassPermissions). In bypass mode a resumed
    session runs every command, including edits and deletions, without asking.
    Choose another mode with --permission-mode MODE (e.g. acceptEdits, manual).
  * Remote Control (default: on). With Remote Control on, the resumed session
    can be read and driven from any device logged in to your Claude account.
    Turn it off with --remote-control off.

Resurrection repeats up to the hop cap (default 10); `reset` raises it, `stop`
ends the lineage. Nothing was registered. Run the same command again to
register; this warning is not shown again.
WARN
}

cmd_register() {
  rr_require_user "register" || return 3
  local rc="on" perm="bypassPermissions"
  local -a sessions=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --remote-control) rc="${2:-}"; shift 2 || { echo "--remote-control needs on|off" >&2; return 2; } ;;
      --remote-control=*) rc="${1#*=}"; shift ;;
      --permission-mode) perm="${2:-}"; shift 2 || { echo "--permission-mode needs a value" >&2; return 2; } ;;
      --permission-mode=*) perm="${1#*=}"; shift ;;
      -*) echo "unknown option $1" >&2; return 2 ;;
      *) sessions+=("$1"); shift ;;
    esac
  done
  case "$rc" in on|true|yes) rc=true ;; off|false|no) rc=false ;;
    *) echo "--remote-control must be on or off, not '$rc'" >&2; return 2 ;; esac
  [[ " $RR_PERM_MODES " == *" $perm "* ]] || {
    echo "--permission-mode must be one of: $RR_PERM_MODES (got '$perm')" >&2; return 2; }
  [[ -n "${SLURM_JOB_ID:-}" ]] || { echo "SLURM_JOB_ID is not set -- run inside a SLURM batch job." >&2; return 1; }
  [[ -n "${TMUX:-}" ]] || {
    echo "not inside tmux. slurm-resurrect rebuilds whole tmux sessions, so run" >&2
    echo "Claude inside a tmux session and register from any pane of it." >&2
    return 1; }

  rr_ensure_dirs
  if [[ ! -f "$RR_HOME/warning_shown" ]]; then
    first_use_warning
    date -Iseconds > "$RR_HOME/warning_shown"
    return 4
  fi

  rr_ensure_config
  rr_refresh_job_meta
  rr_sync_scripts || rr_log "WARNING: could not copy scripts to $RR_STABLE_SCRIPTS"
  prune_stale
  local sock="${TMUX%%,*}"
  [[ ${#sessions[@]} -gt 0 ]] || sessions=("$(current_session "$sock")")

  local dir="$RR_REG_ROOT/$SLURM_JOB_ID"; mkdir -p "$dir/snapshot"
  local s san added=0 via="terminal"
  [[ "${RR_CALLER:-}" == "user-prompt-hook" ]] && via="slash-command"
  for s in "${sessions[@]}"; do
    if [[ -z "$s" ]] || ! tmux -S "$sock" has-session -t "=$s" 2>/dev/null; then
      echo "no tmux session '$s' on socket $sock -- skipping" >&2; continue
    fi
    san=$(sanitize "$s")
    jq -n --arg s "$s" --arg sock "$sock" --arg job "$SLURM_JOB_ID" --arg san "$san" \
          --arg perm "$perm" --argjson rc "$rc" --arg via "$via" \
      '{tmux_session:$s, tmux_socket:$sock, sanitized:$san,
        registered_in_job:$job, registered_at:(now|todate), registered_via:$via,
        permission_mode:$perm, remote_control:$rc}' > "$dir/$san.json"
    RR_SELFREG_DIR="$dir/panes" bash "$RR_SCRIPTS_DIR/rr_snapshot.sh" "$sock" "$s" "$dir/snapshot/$san.json" \
      || rr_log "registered '$s' but initial snapshot failed"
    echo "registered tmux session '$s' in job $SLURM_JOB_ID (permission mode $perm, remote control $([[ $rc == true ]] && echo on || echo off)); it will be resurrected (all windows/panes/Claude sessions)."
    added=$((added+1))
  done
  [[ $added -gt 0 ]] || { echo "nothing registered." >&2; return 1; }
  [[ "${RR_NO_COORDINATOR:-0}" != "1" ]] && ensure_coordinator "$SLURM_JOB_ID"
  return 0
}

cmd_remove() {
  : "${SLURM_JOB_ID:?not set}"
  local s="${1:-}"
  [[ -z "$s" && -n "${TMUX:-}" ]] && s=$(current_session "${TMUX%%,*}")
  [[ -n "$s" ]] || { echo "provide a tmux session name (or run inside tmux)"; return 1; }
  local san; san=$(sanitize "$s")
  local dir="$RR_REG_ROOT/$SLURM_JOB_ID"
  if [[ -f "$dir/$san.json" ]]; then
    rm -f "$dir/$san.json" "$dir/snapshot/$san.json"
    echo "removed tmux session '$s'; it will NOT be resurrected."
  else
    echo "tmux session '$s' was not registered in job $SLURM_JOB_ID"
  fi
}

# Self-message for THIS Claude session, delivered after resurrection. It is
# ALWAYS delivered if it exists -- a note is written only by an agent that chose
# to wind down, so it is that agent's own instruction to itself, and the old
# rule that discarded it for busy/shell panes threw away exactly the notes most
# likely to matter.
#
# Keyed by the tmux PANE id where possible: a pane is stable for the life of the
# job, whereas a session id can change under the same process and would orphan a
# note keyed by it. Pane-keyed notes live inside the job's registry dir, so they
# are cleaned up with it instead of accumulating forever.
cmd_note() {
  [[ $# -gt 0 ]] || { echo "usage: note \"<what to do next>\"" >&2; return 2; }
  if [[ -n "${TMUX_PANE:-}" && -n "${SLURM_JOB_ID:-}" ]]; then
    local dir="$RR_REG_ROOT/$SLURM_JOB_ID/notes"; mkdir -p "$dir"
    printf '%s' "$*" > "$dir/$(sanitize "$TMUX_PANE").txt"
    echo "self-message saved for this pane; it will be delivered after resurrection."
  else
    : "${CLAUDE_CODE_SESSION_ID:?not set -- run inside a claude session in tmux}"
    mkdir -p "$RR_HOME/notes"
    printf '%s' "$*" > "$RR_HOME/notes/${CLAUDE_CODE_SESSION_ID}.txt"
    echo "self-message saved; it will be delivered after resurrection."
  fi
}

cmd_list() {
  local job="${1:-${SLURM_JOB_ID:-}}"; : "${job:?provide a job id}"
  local d="$RR_REG_ROOT/$job" f
  [[ -d "$d" ]] || { echo "(no registry for job $job)"; return 0; }
  shopt -s nullglob
  local any=0
  for f in "$d"/*.json; do
    any=1
    local san; san=$(basename "$f" .json)
    local snap="$d/snapshot/$san.json" claudes="?"
    [[ -f "$snap" ]] && claudes=$(jq '[.windows[].panes[] | select(.claude)] | length' "$snap" 2>/dev/null)
    jq -c --arg c "$claudes" '{tmux_session, tmux_socket, permission_mode, remote_control, claude_panes:$c}' "$f"
  done
  shopt -u nullglob
  [[ $any -eq 1 ]] || echo "(empty)"
}

cmd_count() { local job="${1:-${SLURM_JOB_ID:-}}"; : "${job:?provide a job id}"; rr_reg_count "$job"; }

cmd_timeleft() {
  : "${SLURM_JOB_ID:?not set}"; rr_ensure_config
  local left; left=$(squeue -h -j "$SLURM_JOB_ID" -o '%L' 2>/dev/null)
  [[ -z "$left" ]] && { echo "job not in queue"; return 1; }
  local left_s th; left_s=$(rr_parse_time_to_seconds "$left")
  th=$(rr_cfg_get '.pause_threshold_seconds')
  echo "time_left=${left} (${left_s}s); pause_threshold=${th}s"
  if [[ $left_s -le $th ]]; then
    echo "STATUS: PAST THRESHOLD -- wind down now (checkpoint with 'note', stop spawning work); you will be resurrected."
  else
    echo "STATUS: ok -- $((left_s-th))s of working runway before the wind-down threshold."
  fi
}

cmd_status() {
  rr_ensure_config
  echo "=== config ($RR_CONFIG) ==="; jq . "$RR_CONFIG"
  local job="${SLURM_JOB_ID:-}"
  [[ -n "$job" ]] && { echo "=== registered tmux sessions in job $job ==="; cmd_list "$job"; }
}

cmd_stop() {
  rr_ensure_config
  rr_cfg_set '.stop_requested=true'
  local pending; pending=$(rr_cfg_get '.pending_resurrection_jobid // empty')
  if [[ -n "$pending" && "$pending" != "null" ]]; then
    scancel "$pending" 2>/dev/null && rr_log "scancelled pending $pending"
    rr_cfg_set '.pending_resurrection_jobid=null'
  fi
  rr_notify "Self-resurrection STOPPED by request. Cancelled pending successor ${pending:-none}."
  echo "resurrection disabled for this lineage."
}

cmd_reset() {
  rr_require_user "reset" || return 3
  rr_ensure_config
  local max="${1:-}"
  [[ -z "$max" || "$max" =~ ^[0-9]+$ ]] || { echo "reset N: N must be a number" >&2; return 2; }
  # Also clear any stale pending pointer so a fresh lineage can queue a successor.
  if [[ -n "$max" ]]; then rr_cfg_set ".resurrection_count=0 | .stop_requested=false | .pending_resurrection_jobid=null | .max_resurrections=$max"
  else rr_cfg_set '.resurrection_count=0 | .stop_requested=false | .pending_resurrection_jobid=null'; fi
  echo "reset: count=0, stop=false, pending cleared${max:+, max=$max}"
}

cmd_set_notify() {
  rr_require_user "set-notify" || return 3
  rr_ensure_config
  local tmp; tmp=$(mktemp)
  jq --arg c "$*" '.notify_cmd=(if $c=="" then null else $c end)' "$RR_CONFIG" > "$tmp" && mv "$tmp" "$RR_CONFIG"
  echo "notify_cmd set."
}

# Lineage settings a user may change. Numbers are stored as numbers.
RR_SET_KEYS="queue_mode early_lead_seconds handoff_timeout_seconds handoff_grace_seconds
snapshot_interval_seconds pause_threshold_seconds winddown_threshold_seconds
launch_cmd sbatch_extra account partition default_time_limit nodes ntasks cpus_per_task
context_window_suffix core_scripts_dir"
cmd_set() {
  if [[ $# -lt 2 ]]; then
    echo "usage: set <key> <value>. Keys:"; echo "$RR_SET_KEYS" | tr ' ' '\n' | sed '/^$/d; s/^/  /'
    [[ $# -eq 0 ]] && return 0 || return 2
  fi
  rr_require_user "set" || return 3
  rr_ensure_config
  local key="$1"; shift; local val="$*"
  [[ " $(echo $RR_SET_KEYS) " == *" $key "* ]] || { echo "unknown key '$key'" >&2; return 2; }
  case "$key" in
    queue_mode)
      [[ "$val" == afterany || "$val" == early ]] || { echo "queue_mode is afterany or early" >&2; return 2; }
      rr_cfg_set ".queue_mode=\"$val\"" ;;
    *_seconds|nodes|ntasks|cpus_per_task)
      [[ "$val" =~ ^[0-9]+$ ]] || { echo "$key must be a whole number" >&2; return 2; }
      rr_cfg_set ".$key=$val" ;;
    context_window_suffix)
      jq -e 'type=="object"' <<<"$val" >/dev/null 2>&1 || { echo "context_window_suffix must be a JSON object" >&2; return 2; }
      local tmp; tmp=$(mktemp); jq --argjson v "$val" '.context_window_suffix=$v' "$RR_CONFIG" > "$tmp" && mv "$tmp" "$RR_CONFIG" ;;
    *)
      local tmp; tmp=$(mktemp); jq --arg k "$key" --arg v "$val" '.[$k]=$v' "$RR_CONFIG" > "$tmp" && mv "$tmp" "$RR_CONFIG" ;;
  esac
  echo "$key = $(jq -c --arg k "$key" '.[$k]' "$RR_CONFIG")"
}

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

sub="${1:-}"; shift 2>/dev/null || true
case "$sub" in
  register|add) cmd_register "$@" ;;
  remove)      cmd_remove "$@" ;;
  note)        cmd_note "$@" ;;
  list)        cmd_list "$@" ;;
  count)       cmd_count "$@" ;;
  timeleft)    cmd_timeleft ;;
  status)      cmd_status ;;
  stop)        cmd_stop ;;
  reset)       cmd_reset "$@" ;;
  set)         cmd_set "$@" ;;
  set-notify)  cmd_set_notify "$@" ;;
  help|"")     usage ;;
  *) echo "unknown command '$sub'" >&2; usage >&2; exit 2 ;;
esac
