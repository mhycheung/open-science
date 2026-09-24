#!/bin/bash
# Shared library sourced by the other rr_* scripts.
# Provides: paths, config access + auto-detection, time parsing, registry
# counting, the user-only gate, and best-effort notifications. No side effects on
# source beyond defining variables/functions.

# State dir: RR_STATE_DIR, else the XDG state dir. Every script reads this, so a
# test (or a second lineage) can use its own state by exporting RR_STATE_DIR.
: "${RR_STATE_DIR:=${XDG_STATE_HOME:-$HOME/.local/state}/slurm-resurrect}"
RR_HOME="$RR_STATE_DIR"
RR_CONFIG="$RR_HOME/rr_config.json"
RR_REG_ROOT="$RR_HOME/registry"
RR_LOCK_DIR="$RR_HOME/locks"
RR_HOOK_DIR="$RR_HOME/hooks"

# Directory holding these scripts. Generated successor jobs do not use it: they
# run the copy in $RR_HOME/scripts (see rr_sync_scripts), which survives plugin
# updates that move or delete the plugin directory.
RR_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RR_STABLE_SCRIPTS="$RR_HOME/scripts"

rr_log() { echo "[$(date -Iseconds)] $*" >&2; }

rr_ensure_dirs() { mkdir -p "$RR_HOME" "$RR_REG_ROOT" "$RR_LOCK_DIR" "$RR_HOOK_DIR"; }

# Copy the scripts to $RR_HOME/scripts, so queued successor jobs and the
# wind-down message name a path that does not change when the plugin is updated.
# Atomic: build a temp dir, then swap it in.
rr_sync_scripts() {
  [[ "$RR_SCRIPTS_DIR" == "$RR_STABLE_SCRIPTS" ]] && return 0
  rr_ensure_dirs
  local tmp; tmp=$(mktemp -d "$RR_HOME/.scripts.XXXXXX") || return 1
  cp -p "$RR_SCRIPTS_DIR"/*.sh "$tmp"/ || { rm -rf "$tmp"; return 1; }
  rm -rf "$RR_STABLE_SCRIPTS.old"
  [[ -d "$RR_STABLE_SCRIPTS" ]] && mv "$RR_STABLE_SCRIPTS" "$RR_STABLE_SCRIPTS.old"
  mv "$tmp" "$RR_STABLE_SCRIPTS" && rm -rf "$RR_STABLE_SCRIPTS.old"
}

# Detect the current job's settings so the plugin needs no cluster-specific
# configuration: account, partition, time limit, node/task/cpu counts. The
# SLURM_* environment of the job is preferred; squeue fills in what it lacks.
rr_detect_job_meta() {
  RR_DET_ACCOUNT=""; RR_DET_PART=""; RR_DET_TL=""; RR_DET_NODES=""
  RR_DET_NTASKS=""; RR_DET_CPT=""
  local jid="${SLURM_JOB_ID:-}" a p l d
  [[ -n "$jid" ]] || return 0
  read -r a p l d <<< "$(squeue -h -j "$jid" -o '%a %P %l %D' 2>/dev/null)"
  RR_DET_ACCOUNT="${SLURM_JOB_ACCOUNT:-$a}"
  RR_DET_PART="${SLURM_JOB_PARTITION:-$p}"
  RR_DET_TL="$l"
  RR_DET_NODES="${SLURM_JOB_NUM_NODES:-${d:-1}}"
  RR_DET_NTASKS="${SLURM_NTASKS:-1}"
  RR_DET_CPT="${SLURM_CPUS_PER_TASK:-}"
}

# Create the config on first use. Account, partition, time limit and job size
# come from the current job (rr_refresh_job_meta); everything else has a
# cluster-independent default. Env overrides: RR_ACCOUNT, RR_PARTITION,
# RR_TIME_LIMIT, RR_NODES, RR_NTASKS, RR_CPUS_PER_TASK, RR_MAX_RESURRECTIONS,
# RR_PAUSE_THRESHOLD, RR_WINDDOWN, RR_NOTIFY_CMD, RR_LAUNCH_CMD, RR_QUEUE_MODE.
rr_ensure_config() {
  rr_ensure_dirs
  if [[ ! -f "$RR_CONFIG" ]]; then
    jq -n \
      --arg notify "${RR_NOTIFY_CMD:-}" \
      --arg launch "${RR_LAUNCH_CMD:-claude}" \
      --arg qmode "${RR_QUEUE_MODE:-afterany}" \
      --argjson max "${RR_MAX_RESURRECTIONS:-10}" \
      --argjson th "${RR_PAUSE_THRESHOLD:-120}" \
      --argjson wd "${RR_WINDDOWN:-1800}" \
      '{account:"", partition:"", nodes:1, ntasks:1, cpus_per_task:null,
        default_time_limit:"24:00:00", sbatch_extra:"",
        launch_cmd:$launch,
        queue_mode:$qmode, early_lead_seconds:14400,
        handoff_timeout_seconds:180, handoff_grace_seconds:0,
        snapshot_interval_seconds:300,
        max_resurrections:$max, resurrection_count:0,
        stop_requested:false, pause_threshold_seconds:$th,
        winddown_threshold_seconds:$wd, context_window_suffix:{},
        core_scripts_dir:"",
        notify_cmd:(if $notify=="" then null else $notify end),
        job_lineage:[], pending_resurrection_jobid:null}' > "$RR_CONFIG"
    rr_log "created $RR_CONFIG"
    rr_refresh_job_meta
  fi
}

# Re-read account / partition / time limit / job size from the job the user is
# registering from, so a config written on another account or cluster never
# submits with stale values. RR_* env overrides win.
rr_refresh_job_meta() {
  rr_detect_job_meta
  [[ -n "${SLURM_JOB_ID:-}" || -n "${RR_ACCOUNT:-}${RR_PARTITION:-}" ]] || return 0
  local tmp; tmp=$(mktemp)
  jq --arg a "${RR_ACCOUNT:-$RR_DET_ACCOUNT}" --arg p "${RR_PARTITION:-$RR_DET_PART}" \
     --arg tl "${RR_TIME_LIMIT:-${RR_DET_TL:-}}" \
     --arg n "${RR_NODES:-${RR_DET_NODES:-}}" --arg t "${RR_NTASKS:-${RR_DET_NTASKS:-}}" \
     --arg c "${RR_CPUS_PER_TASK:-${RR_DET_CPT:-}}" \
     '(if $a  != "" then .account=$a else . end)
      | (if $p  != "" then .partition=$p else . end)
      | (if $tl != "" and $tl != "UNLIMITED" and $tl != "INVALID" then .default_time_limit=$tl else . end)
      | (if ($n|test("^[0-9]+$")) then .nodes=($n|tonumber) else . end)
      | (if ($t|test("^[0-9]+$")) then .ntasks=($t|tonumber) else . end)
      | (if ($c|test("^[0-9]+$")) then .cpus_per_task=($c|tonumber) else .cpus_per_task=null end)' \
     "$RR_CONFIG" > "$tmp" && mv "$tmp" "$RR_CONFIG"
}

rr_cfg_get() { jq -r "$1" "$RR_CONFIG"; }
rr_cfg_set() { local tmp; tmp=$(mktemp); jq "$1" "$RR_CONFIG" > "$tmp" && mv "$tmp" "$RR_CONFIG"; }

rr_parse_time_to_seconds() {
  local t="$1" days=0
  # normalize SLURM sentinels
  [[ "$t" == "UNLIMITED" || "$t" == "INVALID" || -z "$t" ]] && { echo 0; return; }
  if [[ "$t" == *-* ]]; then days="${t%%-*}"; t="${t#*-}"; fi
  IFS=: read -ra p <<< "$t"
  local h=0 m=0 s=0
  case ${#p[@]} in
    3) h=${p[0]}; m=${p[1]}; s=${p[2]} ;;
    2) m=${p[0]}; s=${p[1]} ;;
    1) s=${p[0]} ;;
  esac
  echo $(( 10#$days*86400 + 10#$h*3600 + 10#$m*60 + 10#$s ))
}

rr_reg_count() {
  local j="$1" d="$RR_REG_ROOT/$1"
  [[ -d "$d" ]] || { echo 0; return; }
  find "$d" -maxdepth 1 -name '*.json' -type f 2>/dev/null | wc -l | tr -d ' '
}

# Detect this shell's tmux socket + session so a coordinator can send-keys to it.
# Sets RR_TMUX_L (socket name for `tmux -L`) and RR_TMUX_TARGET (session name).
rr_tmux_coords() {
  RR_TMUX_L=""; RR_TMUX_TARGET=""
  [[ -n "${TMUX:-}" ]] || return 0
  RR_TMUX_L=$(basename "${TMUX%%,*}")
  RR_TMUX_TARGET=$(tmux display-message -p '#S' 2>/dev/null)
}

# --- Claude session discovery (shared by snapshot + coordinator) -------------
# Config dirs holding per-session state (sessions/<pid>.json). Override with
# RR_CONFIG_DIRS (space-separated). The live process's own CLAUDE_CONFIG_DIR is
# always tried first (rr_resolve_claude); these are the fallbacks.
rr_config_dirs() {
  local -a _d
  read -ra _d <<< "${RR_CONFIG_DIRS:-${CLAUDE_CONFIG_DIR:-$HOME/.claude} $HOME/.claude}"
  printf '%s\n' "${_d[@]}" | awk '!seen[$0]++'
}

# The command that starts Claude in a rebuilt pane. Config key `launch_cmd`
# (default `claude`), env RR_LAUNCH_CMD overrides. It may be a wrapper script or
# a shell function defined in the pane's interactive shell; the rebuild appends
# --model / --resume / --permission-mode / --remote-control to it.
rr_launch_cmd() {
  local c="${RR_LAUNCH_CMD:-}"
  [[ -n "$c" ]] || c=$(jq -r '.launch_cmd // empty' "$RR_CONFIG" 2>/dev/null)
  printf '%s' "${c:-claude}"
}

# --- the user-only gate ------------------------------------------------------
# Registering a tmux session (and the other lineage-extending commands: reset,
# set, set-notify) is the USER's decision. An agent must never opt a session in
# by itself. Two ways count as the user:
#   * a shell with no Claude process among its ancestors and no CLAUDECODE in its
#     environment (the user typing in a terminal pane);
#   * the plugin's UserPromptSubmit hook, which Claude Code runs only when the
#     user submits a prompt, and which runs the command only when that prompt is
#     the `/slurm-resurrect:resurrect` slash command. The model cannot invoke
#     that skill (disable-model-invocation) and cannot fire the hook.
# Everything else -- a command run by an agent through its Bash tool -- is
# refused, logged to $RR_HOME/refused.log and reported through rr_notify.
#
# This stops accidental and well-meant self-registration. It is not a security
# boundary: an agent running as the same uid that deliberately imitates the
# hook (RR_CALLER) can get past it. The refusal message tells it not to.
rr_has_claude_ancestor() {
  local pid="${1:-$$}" comm ppid n=0
  while [[ -n "$pid" && "$pid" -gt 1 && $n -lt 64 ]]; do
    comm=$(ps -o comm= -p "$pid" 2>/dev/null | tr -d ' ')
    [[ "$comm" == claude ]] && return 0
    ppid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
    [[ -n "$ppid" && "$ppid" != "$pid" ]] || return 1
    pid="$ppid"; n=$((n+1))
  done
  return 1
}

rr_caller_is_agent() {
  [[ "${RR_CALLER:-}" == "user-prompt-hook" ]] && return 1
  [[ -n "${CLAUDECODE:-}" ]] && return 0
  rr_has_claude_ancestor "$$"
}

rr_require_user() {  # <what> -> 0 if the caller is the user, else 3 (refused)
  local what="$1"
  rr_caller_is_agent || return 0
  mkdir -p "$RR_HOME" 2>/dev/null
  echo "[$(date -Iseconds)] REFUSED '$what' from an agent (pid $$, job ${SLURM_JOB_ID:-none}, pane ${TMUX_PANE:-none})" \
    >> "$RR_HOME/refused.log" 2>/dev/null
  cat >&2 <<MSG
REFUSED: '$what' is the user's decision and cannot be run by an agent.
This command was started from inside a Claude Code session (CLAUDECODE is set or
a 'claude' process is an ancestor). Do not work around this check. Tell the user
they can run it themselves, either by typing
    /slurm-resurrect:resurrect $what
in Claude Code, or by running this script in a plain terminal pane.
The attempt has been logged to $RR_HOME/refused.log.
MSG
  [[ -f "$RR_CONFIG" ]] && rr_notify "slurm-resurrect refused an agent's attempt to run '$what' (job ${SLURM_JOB_ID:-none}, pane ${TMUX_PANE:-none})."
  return 3
}

# --- remote-control session name ---------------------------------------------
# The name a resurrected pane should claim in the Claude app / claude.ai list.
#
# Why this exists: with no name given, Claude DERIVES one per process
# (`<host>-b4`, `<host>-d9`, ...) -- a new random suffix on every launch, so a
# pane that has hopped three times has appeared under three unrelated names and
# the entry you had open is not obviously the same session. Verified 2026-08-18:
# `--remote-control <name>` is IGNORED when it comes after the wrappers' own
# `--remote-control ""` (first flag wins; the session came back `derived` again),
# but the env var CLAUDE_CODE_SESSION_NAME does set it (name=rr-stable-test,
# nameSource=null), and composes with the wrapper because it is not argv.
#
# Policy: keep a name the user/session chose explicitly (nameSource != derived);
# otherwise mint one from the tmux coordinates, which the rebuild reproduces
# exactly -- so the SAME pane keeps the SAME name across every hop.
rr_rc_name() {  # <config_dir> <claude_pid> <tmux_session> <window_idx> <pane_idx>
  local cdir="$1" cpid="$2" sess="$3" w="$4" p="$5" f name src
  f="$cdir/sessions/$cpid.json"
  if [[ -f "$f" ]]; then
    name=$(jq -r '.name // empty' "$f" 2>/dev/null)
    src=$(jq -r '.nameSource // empty' "$f" 2>/dev/null)
    if [[ -n "$name" && "$src" != "derived" ]]; then echo "$name"; return 0; fi
  fi
  rr_rc_name_for_pane "$sess" "$w" "$p"
}

# Stable per-pane name: tmux session + window + pane. No hostname, no job id, no
# pid -- all of those change on a hop, which is the whole problem.
rr_rc_name_for_pane() {  # <tmux_session> <window_idx> <pane_idx>
  local sess; sess=$(printf '%s' "${1:-s}" | tr -c 'A-Za-z0-9._-' '-')
  printf 'rr-%s-w%sp%s\n' "$sess" "${2:-0}" "${3:-0}"
}

# All descendant pids of a process (depth-first) -- finds claude even when the
# pane shell wraps it (e.g. the claude-auto-retry node launcher).
rr_descendants() { local p="$1" k; for k in $(pgrep -P "$p" 2>/dev/null); do echo "$k"; rr_descendants "$k"; done; }

# The env value of a running process, or empty. Reads /proc/<pid>/environ (the
# process's initial environment).
rr_proc_env() { tr '\0' '\n' < "/proc/$1/environ" 2>/dev/null | grep -m1 "^$2=" | cut -d= -f2-; }

# --- model + context-window recovery -----------------------------------------
# A context-window selector like the `[1m]` in `--model claude-opus-5[1m]` is
# CLIENT-side: the CLI consumes it and the API echoes back only the bare model id
# ("claude-opus-5"). So a transcript can NEVER reveal it -- the only live source
# is the claude process's own argv.
rr_proc_model() {  # <pid> -> that process's --model value, or empty
  local pid="${1:-}" a next=0 v=""
  [[ -n "$pid" && -r "/proc/$pid/cmdline" ]] || { printf ''; return; }
  while IFS= read -r a; do
    if (( next )); then v="$a"; next=0; continue; fi
    case "$a" in
      --model)   next=1 ;;
      --model=*) v="${a#--model=}" ;;
    esac
  done < <(tr '\0' '\n' < "/proc/$pid/cmdline" 2>/dev/null)
  printf '%s' "$v"
}

# The `[...]` part of a model string ("claude-opus-5[1m]" -> "[1m]"), else empty.
rr_model_suffix() { local m="${1:-}"; [[ "$m" == *"["*"]" ]] && printf '[%s' "${m#*\[}" || printf ''; }

# Context-window policy applied at relaunch: model-id substrings that should
# always resume with a given suffix. Config key `context_window_suffix`, e.g.
#   {"opus": "[1m]", "fable": "[1m]"}
# Env RR_MODEL_SUFFIX_MAP (same JSON) overrides the config. A suffix already
# present on the model always wins -- the policy only fills a missing one, which
# is exactly the case the transcript can't recover.
rr_apply_model_policy() {  # <model> -> <model{+suffix}>
  local m="${1:-}" map sfx
  [[ -n "$m" && "$m" != "null" ]] || { printf '%s' "$m"; return; }
  [[ -z "$(rr_model_suffix "$m")" ]] || { printf '%s' "$m"; return; }
  map="${RR_MODEL_SUFFIX_MAP:-}"
  [[ -n "$map" ]] || map=$(jq -c '.context_window_suffix // {}' "$RR_CONFIG" 2>/dev/null)
  [[ -n "$map" && "$map" != "null" && "$map" != "{}" ]] || { printf '%s' "$m"; return; }
  sfx=$(jq -r --arg m "$m" \
    'to_entries | map(select(.key as $k | $m | contains($k))) | .[0].value // empty' <<<"$map" 2>/dev/null)
  printf '%s%s' "$m" "$sfx"
}

# --- pane -> session cache ---------------------------------------------------
# Maps a tmux PANE ID to the Claude session id last seen running in it. Written
# automatically by rr_resolve_claude every time it reads a live session's state
# file, so nothing has to self-register: the cache is a by-product of ordinary
# snapshotting. Keyed by pane id -- stable for the life of a pane and never
# reused by a running server -- so an entry can never migrate to a different
# pane, which an index-keyed record can and did.
#
# It exists for exactly one case: a LIVE claude (typically idle) whose
# sessions/<pid>.json was deleted by the shared-FS cross-node cleanup. It never
# grants claude-ness to a pane -- only a live process does that.
# Directory: $RR_SELFREG_DIR (registry/<job>/panes), when the caller sets it.
rr_pane_key() { printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'; }

rr_pane_cache_put() {  # <pane_id> <session_id> <config_dir>
  local paneid="${1:-}" sid="${2:-}" cdir="${3:-}" dir="${RR_SELFREG_DIR:-}" f cur
  [[ -n "$dir" && -n "$paneid" && -n "$sid" ]] || return 0
  f="$dir/$(rr_pane_key "$paneid").json"
  cur=$(jq -r '.session_id // empty' "$f" 2>/dev/null)
  [[ "$cur" == "$sid" ]] && return 0          # unchanged -- don't churn the file
  mkdir -p "$dir" 2>/dev/null || return 0
  jq -n --arg sid "$sid" --arg cdir "$cdir" --arg pid "$paneid" \
    '{session_id:$sid, config_dir:$cdir, pane_id:$pid, cached_at:(now|todate)}' \
    > "$f" 2>/dev/null || true
  return 0
}

# Drop a pane's cache entry. Called for every pane that has NO live claude, so a
# mapping can never outlive the session it describes: otherwise a pane whose
# Claude exited, and in which a NEW Claude is later started, could be resumed
# into the old conversation on the strength of a stale entry.
rr_pane_cache_drop() {  # <pane_id>
  local paneid="${1:-}" dir="${RR_SELFREG_DIR:-}"
  [[ -n "$dir" && -n "$paneid" ]] || return 0
  rm -f "$dir/$(rr_pane_key "$paneid").json" 2>/dev/null
  return 0
}

rr_pane_cache_get() {  # <pane_id> -> "session_id|config_dir", or empty
  local paneid="${1:-}" dir="${RR_SELFREG_DIR:-}" f
  [[ -n "$dir" && -n "$paneid" ]] || return 0
  f="$dir/$(rr_pane_key "$paneid").json"
  [[ -f "$f" ]] || return 0
  jq -r 'select(.session_id != null) | [.session_id, (.config_dir // "")] | join("|")' "$f" 2>/dev/null
}

# Given a pane's shell pid, echo "config_dir|claude_pid|session_id|status" for the
# Claude session under it, or return 1.
#
# LIVENESS IS THE GATE. We return success only when a real `claude` process
# exists in the pane's subtree -- so a pane whose Claude was exited (`exit`,
# Ctrl-C) or closed is simply not a Claude pane any more, and no stored record
# can resurrect it. Identity is then resolved:
#   config_dir: the claude process's own environ (the wrapper sets it).
#   session_id: (1) $CONFIGDIR/sessions/<pid>.json -- authoritative and current;
#               (2) the pane-id cache, for a live session whose state file was
#                   deleted underneath it.
#   status:     from the state file if present, else "unknown".
#
# A descendant's $CLAUDE_CODE_SESSION_ID is deliberately NOT consulted: it is
# frozen when that child was spawned and goes stale when the session id changes
# (observed: a child reported 05bbc468 while the state file said 0bbda7ae, both
# real sessions of the same pane). Resuming a superseded conversation is worse
# than admitting we don't know.
rr_resolve_claude() {  # <pane_pid> [pane_id]
  local pane_pid="$1" paneid="${2:-}" pid f sid status cdir cpid="" cached
  for pid in "$pane_pid" $(rr_descendants "$pane_pid"); do
    [[ "$(ps -o comm= -p "$pid" 2>/dev/null)" == claude ]] && { cpid="$pid"; break; }
  done
  [[ -n "$cpid" ]] || return 1
  cdir=$(rr_proc_env "$cpid" CLAUDE_CONFIG_DIR); [[ -n "$cdir" ]] || cdir="$HOME/.claude"
  # (1) state file -- try the environ config dir first, then all known ones
  local d
  for d in "$cdir" $(rr_config_dirs); do
    f="$d/sessions/$cpid.json"; [[ -f "$f" ]] || continue
    sid=$(jq -r '.sessionId // empty' "$f"); status=$(jq -r '.status // "unknown"' "$f")
    [[ -n "$sid" ]] || continue
    rr_pane_cache_put "$paneid" "$sid" "$d"
    printf '%s|%s|%s|%s\n' "$d" "$cpid" "$sid" "$status"; return 0
  done
  # (2) state file gone under a live session -> last mapping seen for THIS pane
  cached=$(rr_pane_cache_get "$paneid")
  if [[ -n "$cached" ]]; then
    sid="${cached%%|*}"; d="${cached#*|}"; [[ -n "$d" ]] || d="$cdir"
    printf '%s|%s|%s|%s\n' "$d" "$cpid" "$sid" "unknown"; return 0
  fi
  return 1
}

# --- pane injection lock -----------------------------------------------------
# EVERY script that types into a Claude pane must hold this lock first: the
# open-science core's jump worker, rr_deliver.sh, and the coordinator's wind-down
# nudge. Two injectors interleaving is not cosmetic: `/clear` only runs when it
# is the FIRST thing in the input buffer, so a nudge landing between a worker's
# C-u and its `/clear` turns the clear into an ordinary message.
#
# The lock file is the core's own (<core state>/lock/<sock>__<pane id>.lock), so
# the two plugins exclude each other. The key is (socket, pane_id), not the
# caller's target string: the core names a pane "%12", this plugin names it
# "session:win.pane", and pane ids are unique only per tmux server.
#
# Falls back to the sanitized target if the pane cannot be resolved (a dead pane
# still gets a stable name, and the caller is about to fail anyway).
rr_pane_lock() {  # <sock> <target> -> lock file path
  local sock="${1:-}" target="${2:-}" paneid
  paneid=$(tmux -S "$sock" display-message -p -t "$target" '#{pane_id}' 2>/dev/null)
  [[ -n "$paneid" ]] || paneid="$target"
  mkdir -p "$RR_OS_STATE/lock" 2>/dev/null || true
  printf '%s/lock/%s.lock' "$RR_OS_STATE" "$(rr_os_key "$sock" "$paneid")"
}

# --- verified prompt delivery ------------------------------------------------
# Typing a prompt into the Claude TUI and pressing Enter is NOT reliable, and the
# failure is silent. Measured 2026-08-08 on pane %2 (Claude Code 2.1.222):
#
#   sent: "/context-management Resume the HX tail-solver campaign: read <path>..."
#   buffer afterwards: "campaign: read <path>..."
#
# The leading 45 characters were GONE. Twenty minutes earlier the same delivery
# lost the WHOLE prompt, so Enter landed on an empty buffer: no message, no turn,
# no error. The pane sat idle holding a finished campaign -- precisely the failure
# a T2 jump exists to prevent.
#
# THE MECHANISM IS NOT ESTABLISHED. The obvious suspect was the slash-command
# menu eating input, but an A/B test on a real TUI (2026-08-09, scratch session,
# 100 cols, idle) typed the identical 422-char `/`-prefixed prompt in ONE
# send-keys and the buffer came out COMPLETE -- the failure did not reproduce.
# What differed on the failing pane: 52 columns, six background shells producing
# output, and a just-cleared session, i.e. heavy redraw. Input loss during redraw
# fits the evidence at least as well, and neither hypothesis is proven.
#
# So the guard here does NOT depend on knowing the cause. Staging the command
# first is cheap insurance; VERIFICATION AND RETRY are what make it safe, and
# they work whatever eats the keystrokes.
#
# It was silent because the callers logged "resume prompt delivered" straight
# after send-keys without checking anything, and deleted their pending-jump
# record on that basis -- so the resurrection machinery would not retry it either.
#
# Everything that types a prompt now goes through rr_pane_deliver, which:
#   1. clears the input buffer for real (one C-u only clears one visual line of a
#      wrapped prompt -- 422 chars needed ~25 of them);
#   2. types any leading /command ALONE and waits for the menu to settle, so the
#      race window closes before the arguments arrive;
#   3. VERIFIES the buffer holds both the head and the tail of the payload,
#      retrying from scratch if not;
#   4. presses Enter and CONFIRMS the buffer drained;
#   5. returns non-zero if any of that failed, so the caller keeps its record.
#
# Comparisons strip all whitespace from both sides: the TUI hard-wraps the buffer
# and re-indents continuation lines, so a literal grep for a fragment fails
# whenever a wrap lands inside it.

# Strip everything invisible. `tr -d '[:space:]'` is NOT enough: measured
# 2026-08-09, the TUI pads its empty prompt line with U+00A0 NO-BREAK SPACE
# (bytes c2 a0), and GNU tr is byte-oriented so [:space:] never matches it. That
# one gap made rr_pane_input_empty report a genuinely empty buffer as non-empty,
# which turned every successful delivery into a "failure" and made the retry loop
# submit the same prompt THREE times. Zero-width and BOM characters are stripped
# for the same reason.
rr_strip_blank() { sed -e 's/\xc2\xa0//g' -e 's/\xe2\x80\x8b//g' -e 's/\xef\xbb\xbf//g' | tr -d '[:space:]'; }

# Whole pane, invisibles removed -- the haystack for buffer checks.
rr_pane_squash() { tmux -S "$1" capture-pane -p -t "$2" 2>/dev/null | rr_strip_blank; }

rr_pane_has() {  # <sock> <target> <fragment>
  local needle; needle=$(printf '%s' "$3" | rr_strip_blank)
  [[ -z "$needle" ]] && return 0
  rr_pane_squash "$1" "$2" | grep -qF -- "$needle"
}

rr_pane_busy() {  # <sock> <target> -- a turn is running
  tmux -S "$1" capture-pane -p -t "$2" 2>/dev/null | grep -q 'esc to interrupt'
}

# A submitted message that Claude Code QUEUED behind a running turn.
#
# MEASURED 2026-08-14, and the cause of the duplicate-wake-up report: pressing
# Enter while a turn is in flight does NOT fail -- the TUI accepts the message
# into a queue and renders "Press up to edit queued messages" ON THE PROMPT
# LINE. Verified by rendering that literal line in a scratch pane and calling
# rr_pane_input_empty on it: it reported the buffer NOT EMPTY. So a delivery
# that had genuinely succeeded was scored a failure, and the retry loop below
# submitted the same prompt again -- up to 4 copies per watcher.
#
# The tmux freeze reported the same day has the same root. Because the buffer
# could never "drain", every attempt ran rr_pane_clear_input to its maximum:
# ~20k keystrokes plus hundreds of capture-pane calls into one pane over ~3
# minutes. tmux's server is single-threaded, so that stalls every pane on the
# socket, not only the target.
#
# QUEUED IS SUBMITTED. The agent will process it at the next turn boundary.
rr_pane_queued() {  # <sock> <target>
  tmux -S "$1" capture-pane -p -t "$2" 2>/dev/null \
    | grep -qiE 'queued message|press up to edit queued'
}

# True when the prompt line holds no un-submitted text. The last prompt-glyph
# line is the live input.
rr_pane_input_empty() {  # <sock> <target>
  local last
  last=$(tmux -S "$1" capture-pane -p -t "$2" 2>/dev/null | grep '❯' | tail -1)
  [[ -z "$last" ]] && return 1
  last="${last#*❯}"
  # The queued-message hint is drawn on the prompt line, but the input BUFFER
  # behind it is empty -- there is nothing to clear and nothing to re-type. See
  # rr_pane_queued: reading this as "still holding text" is exactly the bug that
  # produced duplicate submissions and the pane hammering that froze tmux.
  printf '%s' "$last" | grep -qiE 'queued message|press up to edit queued' && return 0
  [[ -z "$(printf '%s' "$last" | rr_strip_blank)" ]]
}

# The rendered input box is a VIEWPORT, not the buffer.
#
# Claude Code hard-wraps a long prompt to the pane width and, once the wrapped
# form is taller than the box, SCROLLS it to keep the cursor (the END of the text)
# visible. The beginning is then simply not on screen, and capture-pane cannot see
# what was never rendered. The `❯` glyph is drawn on the first VISIBLE line, which
# in that state is a middle line of the payload -- not its start.
#
# Measured 2026-08-09 on a 52-column pane: a 414-char prompt wrapped to 9 lines in
# an 8-line box; the glyph line read "❯ <path>/.../2026" and the first ~60
# characters were unrenderable. Widening the pane to 105 columns WITHOUT touching
# the buffer made the head reappear -- proof the buffer was complete all along and
# only the rendering had scrolled.
#
# This returns the box's visible content (glyph line onward, stopping at the box's
# bottom border), squashed. A contiguous slice of the payload -- ending at its
# tail -- is the CORRECT and expected reading for any prompt that overflows.
rr_pane_input_box() {  # <sock> <target> -> squashed visible input-box text
  local cap ln
  cap=$(tmux -S "$1" capture-pane -p -t "$2" 2>/dev/null) || return 1
  # The LAST glyph line is the live input; earlier ones are transcript text.
  ln=$(printf '%s\n' "$cap" | grep -n '❯' | tail -1 | cut -d: -f1)
  [[ -n "$ln" ]] || return 1
  # index() on the box-drawing byte sequence is locale-independent, unlike a
  # character-class match, so this stops at the border in any LANG.
  printf '%s\n' "$cap" | tail -n +"$ln" | sed '1s/.*❯//' \
    | awk 'index($0,"─"){exit} {print}' | rr_strip_blank
}

# C-u alone is not enough. It clears one VISUAL line of a wrapped prompt, and on
# a busy pane the keystrokes themselves get dropped -- measured on %2, where 40
# of them left the buffer still full. Every few rounds fall back to a bulk
# BSpace burst, which deletes characters regardless of line wrapping.
# Rounds were cut 60 -> 30 and the BSpace burst 400 -> 200 on 2026-08-14. With
# the queued-message false negative fixed above this loop no longer runs to its
# maximum on every attempt, but the maximum itself was still a denial of service
# against a single-threaded tmux server (~20k keystrokes + hundreds of
# capture-pane calls, x4 attempts), which is what froze every pane on the socket.
# 30 rounds still clears a ~1.2k-character prompt, well beyond any resume we send.
rr_pane_clear_input() {  # <sock> <target> -- returns 1 if it could not empty it
  local sock="$1" target="$2" i
  for ((i=0; i<30; i++)); do
    rr_pane_input_empty "$sock" "$target" && return 0
    if (( i % 10 == 9 )); then
      tmux -S "$sock" send-keys -t "$target" -N 200 BSpace 2>/dev/null
      sleep 1
    else
      tmux -S "$sock" send-keys -t "$target" C-u 2>/dev/null
      sleep 0.3
    fi
  done
  rr_pane_input_empty "$sock" "$target"
}

# Insert text via the tmux PASTE buffer instead of send-keys.
#
# Pane %2 was 52 columns wide next to five 104-column panes, and deliveries to it
# failed the head check on every attempt of several consecutive jumps (2026-08-08
# 23:56, 2026-08-09 00:17, 17:17, 18:13) while the same code delivered fine to the
# wide panes. That was originally read as send-keys dropping characters on a
# narrow, re-wrapping buffer, and paste was added as the fix: load-buffer +
# paste-buffer hands the whole string to the pane in ONE write, so there is no
# per-character race to lose.
#
# CORRECTION, measured 2026-08-09: the character-drop diagnosis was wrong, which
# is why adding paste did not stop the failures. Nothing was being dropped. The
# head was missing from the RENDERING only -- see rr_pane_input_box: a narrow pane
# makes the wrapped prompt overflow the input box, which scrolls, and the head
# leaves the screen. Paste is still the right way to write the payload (one write,
# no per-keystroke race), but the bug was in the verification, not the transport.
rr_pane_paste() {  # <sock> <target> <text>
  local sock="$1" target="$2" text="$3" buf="rrdeliver$$"
  printf '%s' "$text" | tmux -S "$sock" load-buffer -b "$buf" - 2>/dev/null || return 1
  tmux -S "$sock" paste-buffer -d -b "$buf" -t "$target" 2>/dev/null || {
    tmux -S "$sock" delete-buffer -b "$buf" 2>/dev/null; return 1; }
  return 0
}

# Deliver a prompt and CONFIRM it was submitted. 0 = submitted, 1 = not.
# The caller must already hold the pane lock (rr_pane_lock).
#
# Attempts 1-2 PASTE the payload; later attempts fall back to the old chunked
# send-keys typing. Paste is the fix for the narrow-pane character drop; typing
# is kept because paste is the path that could, in principle, be collapsed into a
# "[Pasted text]" placeholder by the TUI -- in which case the head check below
# fails and the typed path still gets its turn.
rr_pane_deliver() {  # <sock> <target> <text> [attempts]
  local sock="$1" target="$2" text="$3" attempts="${4:-4}"
  local cmd rest head tail frag i w box want_head want_tail payload
  text=$(printf '%s' "$text" | tr '\n' ' ')          # a newline submits early
  [[ -n "$text" ]] || return 1
  case "$text" in
    /*) cmd="${text%% *}"; rest="${text#"$cmd"}" ;;  # keep the leading space
    *)  cmd=""; rest="$text" ;;
  esac
  frag="${rest# }"
  head=$(printf '%s' "$frag" | cut -c1-24)
  tail=$(printf '%s' "$text" | rev | cut -c1-24 | rev)

  for ((i=1; i<=attempts; i++)); do
    # THE RESUME ALWAYS WINS THE INPUT BOX. This used to abort when the buffer
    # held text this function had not written, reasoning that a missed delivery
    # is recoverable from the pending record while destroyed human input is not.
    # Measured cost of that policy on pane %2: three consecutive jumps delivered
    # NOTHING because an un-submitted line sat in the box (2026-08-08 23:56 and
    # 00:17 fought it and mangled the buffer; 2026-08-09 13:06 aborted cleanly),
    # and the last one left the pane cleared, empty and idle for hours holding a
    # finished job. "Recoverable from the pending record" is only true at the
    # next resurrection, which can be days away -- so in practice the abort did
    # not defer the resume, it lost it.
    #
    # Policy since 2026-08-09, by operator instruction: discard whatever is in
    # the box and deliver. The discarded text is logged verbatim first, so it is
    # never silently vaporised -- it can be recovered by hand from the caller's
    # log (the successor job output or the coordinator log).
    if ! rr_pane_input_empty "$sock" "$target"; then
      # Distinguish foreign text from OUR OWN leftover. A failed attempt leaves
      # its payload in the box, and because the box may be scrolled the visible
      # line is a mid-prompt slice -- which the old code reported as "un-submitted
      # text", making the log read as though a human's message was being destroyed
      # on every retry. It was reporting its own prompt back to itself.
      box=$(rr_pane_input_box "$sock" "$target")
      payload=$(printf '%s' "$text" | rr_strip_blank)
      if [[ -n "$box" && "$payload" == *"$box"* ]]; then
        rr_log "deliver: $target's input box holds a slice of this same payload left by attempt $((i-1)); clearing and rewriting"
      elif ! rr_pane_has "$sock" "$target" "$head"; then
        local stray
        stray=$(tmux -S "$sock" capture-pane -p -t "$target" 2>/dev/null | grep '❯' | tail -1)
        stray="${stray#*❯}"
        rr_log "deliver: DISCARDING un-submitted text in $target's input box to make way for the resume -- was: ${stray# }"
      fi
      rr_pane_clear_input "$sock" "$target" || \
        rr_log "deliver: could not empty $target's input (attempt $i); typing anyway"
    fi

    # Prime the input widget before the payload. Measured on pane %2 (52 cols,
    # six background shells, constant redraw): the FIRST characters of a prompt
    # are dropped there repeatedly, so whatever is typed first is the part at
    # risk. Sacrifice a throwaway keystroke to absorb that instead of the prompt.
    tmux -S "$sock" send-keys -t "$target" "x" 2>/dev/null; sleep 1
    tmux -S "$sock" send-keys -t "$target" C-u 2>/dev/null; sleep 1

    if (( i <= 2 )); then
      # ONE write for the whole payload, leading /command included. By the time
      # the buffer settles the command menu has no match left open, so the Enter
      # below submits the line rather than picking a menu entry.
      if ! rr_pane_paste "$sock" "$target" "$text"; then
        rr_log "deliver: paste-buffer failed on $target (attempt $i/$attempts); retrying"
        continue
      fi
      sleep 3
    else
      if [[ -n "$cmd" ]]; then
        # Let any command menu open and settle BEFORE the arguments arrive.
        tmux -S "$sock" send-keys -t "$target" "$cmd" 2>/dev/null; sleep 3
        if ! rr_pane_has "$sock" "$target" "$cmd"; then
          rr_log "deliver: '$cmd' did not reach $target's buffer (attempt $i/$attempts)"
          continue
        fi
      fi
      # Chunked, so a redraw can eat at most one chunk and the verification below
      # still catches it -- rather than one long burst that loses its opening.
      local off=0 chunk
      while :; do
        chunk=$(printf '%s' "$rest" | cut -c$((off+1))-$((off+60)))
        [[ -z "$chunk" ]] && break
        tmux -S "$sock" send-keys -t "$target" "$chunk" 2>/dev/null
        off=$((off+60)); sleep 0.5
      done
      sleep 2
    fi

    # --- verify the buffer, WITHOUT assuming the whole prompt is on screen ----
    # The old check here required the head to be visible and treated its absence
    # as "buffer LOST THE HEAD". That was a FALSE NEGATIVE on any prompt tall
    # enough to overflow the input box (see rr_pane_input_box): the buffer was
    # complete and ready to submit, but the head had scrolled off. Because nothing
    # was actually wrong, every retry failed identically, and the loop then CLEARED
    # THE BOX and returned failure -- destroying a good delivery. Measured cost:
    # the T2 resumes of 2026-08-09 17:17 and 18:13 were both thrown away that way
    # (4/4 attempts each), stranding a finished campaign on an idle pane for hours.
    #
    # The TAIL is the load-bearing signal instead: the box always scrolls to the
    # cursor, so the END of the payload is on screen whether it overflows or not.
    # A "[Pasted text +N lines]" placeholder or a truncated write fails that check.
    # A missing head is then accepted only once we have confirmed that what IS on
    # screen is a contiguous slice of our own payload.
    box=$(rr_pane_input_box "$sock" "$target")
    want_head=$(printf '%s' "$head" | rr_strip_blank)
    want_tail=$(printf '%s' "$tail" | rr_strip_blank)
    payload=$(printf '%s' "$text" | rr_strip_blank)

    if [[ -z "$box" ]]; then
      rr_log "deliver: input box on $target is empty after writing the payload (attempt $i/$attempts); retrying"
      continue
    fi
    if [[ "$box" != *"$want_tail"* ]]; then
      rr_log "deliver: input box on $target is missing the END of the prompt (attempt $i/$attempts) -- placeholder or truncated write; retrying"
      continue
    fi
    if [[ "$box" != *"$want_head"* ]]; then
      if [[ "$payload" == *"$box"* ]]; then
        rr_log "deliver: prompt head is scrolled out of $target's input box -- visible slice is ${#box} of ${#payload} squashed chars and ends in the tail, so the buffer is COMPLETE; accepting (this is not the 2026-08-08 failure)"
      else
        rr_log "deliver: input box on $target holds text that is NOT this payload (attempt $i/$attempts); retrying"
        continue
      fi
    fi

    tmux -S "$sock" send-keys -t "$target" Enter 2>/dev/null
    # Two independent success signals. A retry that fires after the prompt DID
    # land submits it twice, so treat "the agent started working" as proof even
    # if the buffer check is somehow wrong -- duplicate delivery is worse than a
    # missed confirmation, and the pending record keeps a real miss recoverable.
    for ((w=0; w<20; w++)); do
      sleep 1
      if rr_pane_busy "$sock" "$target"; then
        rr_log "deliver: submitted to $target (attempt $i; pane went busy)"
        return 0
      fi
      # Third success signal, added 2026-08-14. Enter pressed between turns gets
      # the message QUEUED rather than run: accepted, but the pane is neither
      # busy nor showing a drained prompt line. Without this the loop retried and
      # submitted duplicates. See rr_pane_queued.
      if rr_pane_queued "$sock" "$target"; then
        rr_log "deliver: submitted to $target (attempt $i; QUEUED behind the running turn)"
        return 0
      fi
      if rr_pane_input_empty "$sock" "$target"; then
        rr_log "deliver: submitted to $target (attempt $i; buffer drained)"
        return 0
      fi
    done
    rr_log "deliver: Enter did not drain $target's buffer (attempt $i/$attempts)"
  done

  # LEAVE NO UN-SUBMITTED TEXT BEHIND, EVER. Observed on %2, 2026-08-09 00:18:
  # three failed attempts returned with mangled text still in the box, which
  # reads as a message the agent composed and then refused to send -- and a pane
  # holding un-submitted text is exactly what makes the NEXT delivery fail, which
  # is how one stall became three. This clear is therefore unconditional: it no
  # longer spares text a human typed during the attempts (operator instruction,
  # 2026-08-09 -- same rule as above, the resume owns the input box). Whatever is
  # discarded is logged first.
  if ! rr_pane_input_empty "$sock" "$target"; then
    local left
    left=$(tmux -S "$sock" capture-pane -p -t "$target" 2>/dev/null | grep '❯' | tail -1)
    left="${left#*❯}"
    rr_log "deliver: clearing $target's input box after a failed delivery -- discarding: ${left# }"
    rr_pane_clear_input "$sock" "$target" || \
      rr_log "deliver: WARNING -- could not clear $target's buffer after failing; it may still hold text"
  fi
  rr_log "deliver: FAILED to deliver to $target after $attempts attempts -- nothing submitted, buffer cleared"
  return 1
}

# --- coupling to the open-science core's session jumps (optional) --------------
# The open-science plugin's context management clears and resumes a session with
# a detached worker (`jump.sh --worker <request>`). The worker is a process in
# this SLURM job, so it dies at the wall-clock limit, and so does the background
# Bash that wakes a session after a wait jump. This section reads the core's
# jump records so the successor can finish what the limit interrupted. The
# interface is documented in reference/jump-hook.md. Nothing here is required:
# with no core installed there are no records and nothing happens.
#
# Core state dir and pane key, exactly as the core computes them (cm_lib.sh).
RR_OS_STATE="${OPSCI_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/open-science}"
rr_os_key() {  # <sock> <pane_id> -> key
  printf '%s__%s' "$(rr_pane_key "$1")" "$(rr_pane_key "$2")"
}

# pid part of a state_file path "<config>/sessions/<pid>.json"
rr_sf_pid() { local b; b=$(basename "${1:-}" .json); [[ "$b" =~ ^[0-9]+$ ]] && echo "$b"; }

# Epoch seconds of an ISO time, or 0.
rr_iso_epoch() { date -d "${1:-}" +%s 2>/dev/null || echo 0; }

# Find the jump record that matters for one live Claude pane, or nothing.
#   1. A pending record jump/<key>.json whose state_file names this claude pid:
#      a jump in flight (requested / launched / running / unconfirmed / cleared).
#   2. Otherwise the NEWEST finished record jump/done/<key>-<epoch>.json for this
#      pid. It counts only if it is a wait jump (phase done or cleared), not yet
#      handled by an earlier hop, and newer than the session's last activity
#      (transcript mtime, with RR_JUMP_ACTIVITY_SLACK s of slack, default 120):
#      the pane was cleared to wait and nothing has happened in it since, so
#      its waker is the thing the hop is about to kill.
# The pid check stops a record left by an earlier job from matching a new pane
# that happens to get the same socket path and pane id.
# Prints the record as compact JSON with rr_where ("pending"|"done") and rr_src.
rr_jump_find() {  # <sock> <pane_id> <claude_pid> <session_id> <config_dir>
  local sock="$1" paneid="$2" cpid="$3" sid="$4" cdir="$5" key f newest="" e best=0 t act tf
  [[ -n "$paneid" && -n "$cpid" ]] || return 0
  key=$(rr_os_key "$sock" "$paneid")
  f="$RR_OS_STATE/jump/$key.json"
  if [[ -f "$f" ]] && [[ "$(rr_sf_pid "$(jq -r '.state_file // ""' "$f" 2>/dev/null)")" == "$cpid" ]]; then
    jq -c --arg src "$f" '. + {rr_where:"pending", rr_src:$src}' "$f" 2>/dev/null
    return 0
  fi
  shopt -s nullglob
  for f in "$RR_OS_STATE/jump/done/$key"-*.json; do
    e="${f##*-}"; e="${e%.json}"
    [[ "$e" =~ ^[0-9]+$ ]] || continue
    [[ "$(rr_sf_pid "$(jq -r '.state_file // ""' "$f" 2>/dev/null)")" == "$cpid" ]] || continue
    (( e > best )) && { best=$e; newest="$f"; }
  done
  shopt -u nullglob
  [[ -n "$newest" ]] || return 0
  jq -e '.kind=="wait" and (.phase=="done" or .phase=="cleared") and (.rr_handled|not)' \
    "$newest" >/dev/null 2>&1 || return 0
  t=$(rr_iso_epoch "$(jq -r '.phase_at // .requested_at // ""' "$newest")")
  (( t > 0 )) || t=$best
  act=0
  if [[ -n "$sid" ]]; then
    tf=$(find -L "$cdir/projects" -maxdepth 2 -name "$sid.jsonl" -type f 2>/dev/null | head -1)
    [[ -n "$tf" ]] && act=$(stat -c %Y "$tf" 2>/dev/null || echo 0)
  fi
  (( act > t + ${RR_JUMP_ACTIVITY_SLACK:-120} )) && return 0
  jq -c --arg src "$newest" '. + {rr_where:"done", rr_src:$src}' "$newest" 2>/dev/null
  return 0
}

# The core's scripts directory (holding jump.sh and cm_lib.sh), or return 1.
# Order: env RR_CORE_SCRIPTS_DIR; config key core_scripts_dir; a --plugin-dir
# on the claude process's command line; the plugin cache of the config dir.
rr_core_ok() { [[ -n "${1:-}" && -f "$1/jump.sh" && -f "$1/cm_lib.sh" ]]; }
rr_core_scripts() {  # [claude_pid] [config_dir]
  local cpid="${1:-}" cdir="${2:-}" d prev="" a
  d="${RR_CORE_SCRIPTS_DIR:-$(jq -r '.core_scripts_dir // empty' "$RR_CONFIG" 2>/dev/null)}"
  rr_core_ok "$d" && { echo "$d"; return 0; }
  if [[ -n "$cpid" && -r "/proc/$cpid/cmdline" ]]; then
    while IFS= read -r -d '' a; do
      if [[ "$prev" == --plugin-dir ]] && rr_core_ok "$a/scripts"; then
        (cd "$a/scripts" && pwd); return 0
      fi
      prev="$a"
    done < "/proc/$cpid/cmdline"
  fi
  for d in ${cdir:+"$cdir"} $(rr_config_dirs); do
    a=$(ls -dt "$d/plugins/cache/"*/open-science-context/*/scripts 2>/dev/null | head -1)
    rr_core_ok "$a" && { echo "$a"; return 0; }
  done
  return 1
}

# The resume prompt the core uses for a context file.
rr_continue_prompt() { printf '/open-science-context:continue-context %s' "$1"; }

# --- jump inhibit during wind-down ---------------------------------------------
# While <core state>/inhibit_jump_<key> exists, the core's jump.sh refuses new
# jumps for that pane. The coordinator creates one per registered Claude pane at
# wind-down (a jump started then would be cut off by the limit), lists them in
# $RR_HOME/inhibit_<job>.list, and they are removed after the hop, when a
# resurrection is called off, or by the next coordinator's sweep.
rr_inhibit_panes() {  # <job> <message>
  local job="$1" msg="$2" f sock name ppid paneid list="$RR_HOME/inhibit_$1.list" p
  mkdir -p "$RR_OS_STATE" 2>/dev/null || return 0
  shopt -s nullglob
  for f in "$RR_REG_ROOT/$job"/*.json; do
    sock=$(jq -r '.tmux_socket' "$f"); name=$(jq -r '.tmux_session' "$f")
    while IFS=$'\t' read -r ppid paneid; do
      rr_resolve_claude "$ppid" "$paneid" >/dev/null || continue
      p="$RR_OS_STATE/inhibit_jump_$(rr_os_key "$sock" "$paneid")"
      printf '%s\n' "$msg" > "$p" && echo "$p" >> "$list"
    done < <(tmux -S "$sock" list-panes -s -t "$name" -F $'#{pane_pid}\t#{pane_id}' 2>/dev/null)
  done
  shopt -u nullglob
}
rr_uninhibit() {  # <job>
  local list="$RR_HOME/inhibit_$1.list" p
  [[ -f "$list" ]] || return 0
  while IFS= read -r p; do
    [[ "$p" == "$RR_OS_STATE"/inhibit_jump_* ]] && rm -f "$p"
  done < "$list"
  rm -f "$list"
}
# Remove the inhibit files of jobs that are no longer in the queue.
rr_inhibit_sweep() {  # [job to keep]
  local l j
  shopt -s nullglob
  for l in "$RR_HOME"/inhibit_*.list; do
    j=$(basename "$l" .list); j="${j#inhibit_}"
    [[ "$j" == "${1:-}" ]] && continue
    squeue -h -j "$j" -o '%i' 2>/dev/null | grep -q . || rr_uninhibit "$j"
  done
  shopt -u nullglob
}

# Best-effort notification: always appended to $RR_HOME/notifications.log; then,
# if config.notify_cmd is set, that command is run with the message in $RR_MSG
# (set it with `rr_registry.sh set-notify '<cmd>'`). Never fails the caller.
rr_notify() {
  local msg="$1"
  echo "[$(date -Iseconds)] $msg" >> "$RR_HOME/notifications.log"
  local cmd; cmd=$(jq -r '.notify_cmd // empty' "$RR_CONFIG" 2>/dev/null)
  if [[ -n "$cmd" ]]; then
    RR_MSG="$msg" bash -c "$cmd" >/dev/null 2>&1
  fi
  return 0
}
