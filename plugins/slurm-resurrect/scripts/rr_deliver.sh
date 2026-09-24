#!/bin/bash
# Usage: rr_deliver.sh <manifest.jsonl>
#
# Collective post-boot steps for a resurrected job, run once after rr_rebuild.sh
# has launched every pane. Manifest lines are the JSON objects rr_rebuild.sh
# prints: {socket, target, status, session_id, note, note_src, jump, jump_src, ...}.
#
# This lives in a script rather than inlined in the generated sbatch file on
# purpose: SLURM copies a batch script at submit time, so anything inlined there
# is frozen for an already-queued successor, while this file is read fresh when
# the job actually runs.
#
# Steps:
#   1. Dismiss a workspace-trust dialog, and ONLY if one is actually on screen --
#      never a blanket Enter, which would queue an empty message into a session
#      that is already running.
#   2. Finish a session jump of the open-science core that the wall-clock limit
#      interrupted, or wake a pane that was waiting after a wait jump.
#   3. Deliver each pane's checkpoint note.
#   4. Remote Control audit (read only).
#
# Delivery rule for notes: a note is delivered whenever one exists, regardless of
# the pane's status at snapshot. A note is written only by an agent that chose to
# wind down, so it is that agent's own instruction to itself.
#
# Panes with neither a note nor a jump record are left completely silent. An idle
# agent is never poked.
#
# ---------------------------------------------------------------------------
# Step 2, the jump recovery matrix. The record is the core's request file
# (reference/jump-hook.md), found by rr_snapshot.sh; rr_where says whether it
# was pending (jump/<key>.json) or the newest finished one (jump/done/).
# "Cleared" means the snapshot's session id differs from the record's old_sid,
# or the record's phase is `cleared`.
#
#   pending, not cleared      re-drive: write a new request for the REBUILT pane
#                             (sock, pane, key, state_file, old_sid rewritten;
#                             phase launched) and start the core's worker
#                             `jump.sh --worker <request>`. A wait jump is
#                             re-driven as an active jump with the prompt
#                             `/open-science-context:continue-context <context>`: its
#                             waker (a background Bash) died with the old job.
#   pending, cleared          deliver the resume prompt (active: the record's
#                             prompt; wait: the continue-context prompt).
#   done, kind wait           the pane was cleared and waiting when the job
#                             ended; its waker is gone. Deliver the
#                             continue-context prompt so the agent re-arms it.
#
# If the core's jump.sh cannot be found (record field rr_core_scripts, env
# RR_CORE_SCRIPTS_DIR, config core_scripts_dir), a re-drive falls back to
# delivering the resume prompt without clearing the pane, and says so.
#
# Set RR_DELIVER_DRYRUN=1 to log every action instead of performing it.
set -u
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/rr_common.sh"

DRY="${RR_DELIVER_DRYRUN:-0}"

MANIFEST="${1:?manifest jsonl required}"
[[ -s "$MANIFEST" ]] || { echo "rr_deliver: empty manifest; nothing to do"; exit 0; }

# Every injection holds the pane lock (the core's lock file for the pane), so we
# never interleave with a jump worker mid `C-u` + `/clear` + `Enter`.
# NOTE: the lock is taken in a SUBSHELL and released on return. A jump worker
# started here takes the same lock itself, so it must be started OUTSIDE it.
# Returns non-zero if the text could not be VERIFIABLY submitted.
inject() {  # <sock> <target> <text>
  local sock="$1" target="$2" text="$3" lock
  if [[ "$DRY" == "1" ]]; then echo "DRYRUN inject -> $target: $text"; return 0; fi
  lock=$(rr_pane_lock "$sock" "$target")
  (
    exec 9>"$lock"
    flock -w 300 9 || { echo "rr_deliver: could not lock $target within 300s; not injecting"; exit 1; }
    rr_pane_deliver "$sock" "$target" "$text"
  )
}

# Wait for a rebuilt pane to stop being busy before typing into it. The boot
# sleep below is a fixed guess; this is the actual condition.
wait_idle() {  # <sock> <target> [timeout]
  local sock="$1" target="$2" t="${3:-120}" w=0
  while [[ $w -lt $t ]]; do
    tmux -S "$sock" capture-pane -p -t "$target" 2>/dev/null | grep -q 'esc to interrupt' || return 0
    sleep 2; w=$((w+2))
  done
  return 1
}


# Give the rebuilt sessions time to boot before touching their panes.
[[ "$DRY" == "1" ]] || sleep "${RR_BOOT_WAIT:-40}"

# --- 1. trust dialogs, only where one is showing -----------------------------
# The dialog's cursor starts on "No, exit" (MEASURED on Claude Code 2.1.280), so a
# bare Enter would quit the resumed session. Move the cursor with Down until the
# "Yes, I trust this folder" line is the selected one, and only then press Enter.
# If that line never becomes selected, press nothing and report it.
TRUST_SEL='(❯|>)[[:space:]]*(2\.[[:space:]]*)?Yes, I trust'
accept_trust() {  # <sock> <target> -> 0 accepted, 1 could not select "Yes"
  local sock="$1" target="$2" i
  for ((i=0; i<4; i++)); do
    if tmux -S "$sock" capture-pane -p -t "$target" 2>/dev/null | grep -Eq "$TRUST_SEL"; then
      tmux -S "$sock" send-keys -t "$target" Enter 2>/dev/null; return 0
    fi
    tmux -S "$sock" send-keys -t "$target" Down 2>/dev/null
    sleep "${RR_TRUST_KEY_WAIT:-0.5}"
  done
  return 1
}
while IFS= read -r line; do
  [[ -n "$line" ]] || continue
  sock=$(jq -r '.socket // empty' <<<"$line"); target=$(jq -r '.target // empty' <<<"$line")
  [[ -n "$sock" && -n "$target" ]] || continue
  if tmux -S "$sock" capture-pane -p -t "$target" 2>/dev/null | grep -qi 'trust this folder'; then
    if [[ "$DRY" == "1" ]]; then
      echo "DRYRUN trust-dialog accept -> $target"
      continue
    fi
    lock=$(rr_pane_lock "$sock" "$target")
    if ( exec 9>"$lock"; flock -w 60 9 && accept_trust "$sock" "$target" ); then
      echo "accepted the trust dialog in $target"
    else
      echo "rr_deliver: FAILED to select 'Yes, I trust this folder' in $target; pressed nothing"
      rr_notify "slurm-resurrect: the resumed session in $target is stopped at the workspace trust dialog (job ${SLURM_JOB_ID:-?}). Attach and answer it."
    fi
  fi
done < "$MANIFEST"

[[ "$DRY" == "1" ]] || sleep "${RR_SETTLE_WAIT:-8}"

# --- 2. interrupted session jumps --------------------------------------------
# Archive a record once acted on, so no later hop acts on it again.
mark_handled() {  # <record path> <where>
  local f="$1" where="$2" tmp dest
  [[ -f "$f" ]] || return 0
  tmp="$f.tmp.$$"
  if [[ "$where" == done ]]; then
    jq --arg j "${SLURM_JOB_ID:-}" '.rr_handled=$j' "$f" > "$tmp" && mv "$tmp" "$f"
  else
    mkdir -p "$RR_OS_STATE/jump/done"
    dest="$RR_OS_STATE/jump/done/$(basename "$f" .json)-$(date +%s).json"
    jq --arg j "${SLURM_JOB_ID:-}" --arg at "$(date -Iseconds)" \
      '.phase="done" | .phase_at=$at | .rr_handled=$j' "$f" > "$tmp" \
      && mv "$tmp" "$dest" && rm -f "$f"
  fi
  rm -f "$tmp" 2>/dev/null
}

# Re-drive an unfinished jump in the rebuilt pane through the core's worker.
# 0 = worker started (or prompt delivered by the fallback), 1 = failed.
redrive() {  # <sock> <target> <resume prompt> <core dir> <record json>
  local sock="$1" target="$2" rp="$3" core="$4" rec="$5" ppid paneid info cdir cpid sid req
  if ! rr_core_ok "$core"; then
    echo "rr_deliver: open-science core not found; delivering the resume prompt to $target without clearing it"
    inject "$sock" "$target" "$rp"; return
  fi
  if [[ "$DRY" == "1" ]]; then echo "DRYRUN redrive -> $target: $rp (worker $core/jump.sh)"; return 0; fi
  ppid=$(tmux -S "$sock" display-message -p -t "$target" '#{pane_pid}' 2>/dev/null)
  paneid=$(tmux -S "$sock" display-message -p -t "$target" '#{pane_id}' 2>/dev/null)
  if [[ -z "$ppid" ]] || ! info=$(rr_resolve_claude "$ppid" "$paneid"); then
    echo "rr_deliver: no Claude session in $target; cannot re-drive its jump"; return 1
  fi
  IFS='|' read -r cdir cpid sid _ <<< "$info"
  req="$RR_OS_STATE/jump/$(rr_os_key "$sock" "$paneid").json"
  mkdir -p "$RR_OS_STATE/jump"
  jq --arg prompt "$rp" --arg sock "$sock" --arg pane "$paneid" --arg key "$(rr_os_key "$sock" "$paneid")" \
     --arg sf "$cdir/sessions/$cpid.json" --arg sid "$sid" --arg at "$(date -Iseconds)" \
     --arg job "${SLURM_JOB_ID:-}" \
     'del(.rr_where, .rr_src, .rr_core_scripts, .rr_handled, .phase_at)
      | .rr_orig_kind=.kind | .kind="active" | .prompt=$prompt
      | .sock=$sock | .pane=$pane | .key=$key | .state_file=$sf | .old_sid=$sid
      | .requested_at=$at | .phase="launched" | .rr_redriven_in=$job' <<<"$rec" > "$req.tmp" \
    && mv "$req.tmp" "$req" || { echo "rr_deliver: could not write $req"; return 1; }
  setsid nohup bash "$core/jump.sh" --worker "$req" </dev/null >>"$RR_OS_STATE/cm.log" 2>&1 &
  echo "rr_deliver: started the core's jump worker for $target (request $req)"
  return 0
}

jumps=0
while IFS= read -r line; do
  [[ -n "$line" ]] || continue
  jump=$(jq -c '.jump // null' <<<"$line")
  [[ -n "$jump" && "$jump" != "null" ]] || continue
  sock=$(jq -r '.socket // empty' <<<"$line"); target=$(jq -r '.target // empty' <<<"$line")
  [[ -n "$sock" && -n "$target" ]] || continue
  snap_sid=$(jq -r '.session_id // ""' <<<"$line")

  where=$(jq -r '.rr_where // "pending"' <<<"$jump")
  kind=$(jq -r '.kind // ""'             <<<"$jump")
  phase=$(jq -r '.phase // ""'           <<<"$jump")
  ctx=$(jq -r '.context // ""'           <<<"$jump")
  prompt=$(jq -r '.prompt // ""'         <<<"$jump")
  old_sid=$(jq -r '.old_sid // ""'       <<<"$jump")
  src=$(jq -r '.rr_src // ""'            <<<"$jump")
  core="${RR_CORE_SCRIPTS_DIR:-$(jq -r '.rr_core_scripts // ""' <<<"$jump")}"
  rr_core_ok "$core" || core=$(rr_core_scripts) || core=""

  rp="$prompt"
  [[ "$kind" == active && -n "$rp" ]] || rp=$([[ -n "$ctx" ]] && rr_continue_prompt "$ctx")
  if [[ -z "$rp" ]]; then
    echo "rr_deliver: $target has a $where $kind jump record with no prompt and no context file; left alone"
    continue
  fi

  if [[ "$where" == done ]]; then action=wake
  elif [[ "$phase" == cleared || ( -n "$old_sid" && -n "$snap_sid" && "$snap_sid" != "$old_sid" ) ]]; then action=deliver
  else
    case "$phase" in
      requested|launched|running|unconfirmed) action=redrive ;;
      *) echo "rr_deliver: $target: pending jump in phase '$phase'; left alone"; continue ;;
    esac
  fi

  if [[ "$DRY" != "1" ]] && ! wait_idle "$sock" "$target"; then
    echo "rr_deliver: pane $target still busy; leaving its $kind jump ($action) undone"
    continue
  fi

  ok=1
  case "$action" in
    redrive)
      if redrive "$sock" "$target" "$rp" "$core" "$jump"; then
        echo "re-drove interrupted $kind jump for $target"; jumps=$((jumps+1))
      else ok=0; echo "rr_deliver: FAILED to re-drive the $kind jump for $target"; fi ;;
    deliver)
      if inject "$sock" "$target" "$rp"; then
        echo "delivered interrupted resume prompt to $target"; jumps=$((jumps+1))
      else ok=0; echo "rr_deliver: FAILED to deliver the resume prompt to $target"; fi ;;
    wake)
      if inject "$sock" "$target" "$rp"; then
        echo "woke $target: it was waiting after a wait jump and its waker died with the old job"
        jumps=$((jumps+1))
      else ok=0; echo "rr_deliver: FAILED to wake $target after its wait jump"; fi ;;
  esac
  if [[ "$ok" == 1 ]]; then
    [[ "$DRY" != "1" && -n "$src" ]] && mark_handled "$src" "$where"
  else
    rr_notify "slurm-resurrect: could not finish the interrupted session jump in $target (job ${SLURM_JOB_ID:-?}). Type by hand: $rp"
  fi
done < "$MANIFEST"

# --- 3. checkpoint notes -----------------------------------------------------
delivered=0
while IFS= read -r line; do
  [[ -n "$line" ]] || continue
  sock=$(jq -r '.socket // empty' <<<"$line"); target=$(jq -r '.target // empty' <<<"$line")
  sid=$(jq -r '.session_id // empty' <<<"$line")
  note=$(jq -r '.note // ""' <<<"$line"); nsrc=$(jq -r '.note_src // ""' <<<"$line")
  [[ -n "$sock" && -n "$target" && -n "$note" ]] || continue
  if ! inject "$sock" "$target" "$note"; then
    # Keep the note file: an undelivered self-message must stay recoverable.
    echo "rr_deliver: FAILED to deliver the checkpoint note to $target ($sid); note left at ${nsrc:-?}"
    continue
  fi
  if [[ "$DRY" != "1" ]]; then
    [[ -n "$nsrc" ]] && rm -f "$nsrc"
    [[ -n "$sid" ]] && rm -f "$RR_HOME/notes/$sid.txt"   # legacy session-keyed copy
  fi
  delivered=$((delivered+1))
  echo "delivered checkpoint note to $target ($sid)"
done < "$MANIFEST"

echo "rr_deliver: $jumps jump(s) recovered, $delivered note(s) delivered; panes with neither left silent."

# --- 4. Remote Control audit -------------------------------------------------
# Verify, rather than assume, that every rebuilt pane came back reachable from
# the app / claude.ai, and under the name we asked for. Read-only: nothing is
# typed into a pane on the strength of this. The result is written to
# $RR_HOME/rc_status_<job>.txt so the successor script can append it to the
# connect-info notification -- so "remote control isn't on" becomes a line you
# can look at instead of a thing noticed hours later.
#
# The signal is the pane's own state file ($CONFIGDIR/sessions/<claude_pid>.json):
#   name            -- must equal the rc_name we passed, proving the env var took
#   bridgeSessionId -- the app/web session; its URL is claude.ai/code/<id>
# NOTE (verified 2026-08-18): bridgeSessionId is present even WITHOUT
# `--remote-control`, and it is keyed to the Claude session id, so it is stable
# across hops. The name is the part that used to churn.
RC_OUT="$RR_HOME/rc_status_${SLURM_JOB_ID:-manual}.txt"
mkdir -p "$RR_HOME" 2>/dev/null
: > "$RC_OUT" 2>/dev/null || RC_OUT=/dev/null
rc_ok=0; rc_bad=0
while IFS= read -r line; do
  [[ -n "$line" ]] || continue
  sock=$(jq -r '.socket // empty' <<<"$line"); target=$(jq -r '.target // empty' <<<"$line")
  want=$(jq -r '.rc_name // ""' <<<"$line")
  rcon=$(jq -r 'if .remote_control == false then "false" else "true" end' <<<"$line")
  [[ -n "$sock" && -n "$target" ]] || continue
  ppid=$(tmux -S "$sock" display-message -p -t "$target" '#{pane_pid}' 2>/dev/null)
  paneid=$(tmux -S "$sock" display-message -p -t "$target" '#{pane_id}' 2>/dev/null)
  if [[ -z "$ppid" ]] || ! info=$(rr_resolve_claude "$ppid" "$paneid"); then
    echo "$target: NO CLAUDE PROCESS -- not reachable remotely" >> "$RC_OUT"; rc_bad=$((rc_bad+1)); continue
  fi
  IFS='|' read -r cdir cpid _ _ <<< "$info"
  f="$cdir/sessions/$cpid.json"
  got=$(jq -r '.name // ""' "$f" 2>/dev/null)
  bridge=$(jq -r '.bridgeSessionId // ""' "$f" 2>/dev/null)
  if [[ "$rcon" == "false" ]]; then
    echo "$target: remote control off (as registered)" >> "$RC_OUT"; rc_ok=$((rc_ok+1))
  elif [[ -z "$bridge" ]]; then
    echo "$target: REMOTE CONTROL OFF (no bridge session)" >> "$RC_OUT"; rc_bad=$((rc_bad+1))
  elif [[ -n "$want" && "$got" != "$want" ]]; then
    echo "$target: remote OK as '$got' (wanted '$want') https://claude.ai/code/$bridge" >> "$RC_OUT"; rc_bad=$((rc_bad+1))
  else
    echo "$target: remote OK as '$got' https://claude.ai/code/$bridge" >> "$RC_OUT"; rc_ok=$((rc_ok+1))
  fi
done < "$MANIFEST"
echo "rr_deliver: remote control -- $rc_ok pane(s) OK, $rc_bad needing attention (detail in $RC_OUT)"
cat "$RC_OUT"
