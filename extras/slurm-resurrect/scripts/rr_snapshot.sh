#!/bin/bash
# Usage: rr_snapshot.sh <tmux_socket_path> <session_name> [out.json]
#
# Capture EVERYTHING needed to rebuild one tmux session in a later job:
#   * every window (index, name, active flag, exact `window_layout` string);
#   * every pane (index, cwd) in pane-index order;
#   * for panes running a Claude Code session: the session_id (for --resume),
#     which config dir it belongs to and whether the process had CLAUDE_CONFIG_DIR
#     set (restored on relaunch), the model it was last using (transcript base id + the context-window suffix
#     read off the live process's argv), and its live status (busy/idle/shell).
#
# The layout string restores exact pane geometry via `tmux select-layout` on
# rebuild. Non-Claude panes are captured as plain panes (recreated as empty
# shells in the same cwd). Emits JSON to stdout (or <out.json>).
#
# Claude is identified LIVE: a pane counts as a Claude pane only if a running
# `claude` process exists in its process subtree at capture time. Identity then
# comes from that process's $CONFIGDIR/sessions/<pid>.json (falling back to the
# pane-id cache if a cross-node cleanup deleted it). Nothing on disk can mark a
# pane as Claude on its own, so an exited session is never resurrected.
set -u
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/rr_common.sh"

SOCK="${1:?tmux socket path required}"
SESSION="${2:?tmux session name required}"
OUT="${3:-/dev/stdout}"

read -ra CONFIG_DIRS <<< "$(rr_config_dirs | tr '\n' ' ')"

# Per-session checkpoint notes live here (keyed by session_id, hop-stable).
NOTES_DIR="${RR_NOTES_DIR:-$RR_HOME/notes}"

T=(tmux -S "$SOCK")


# Recover the model a session was last using: the base id from its transcript
# (last main-thread, non-sidechain assistant message -- correct even if the user
# switched model mid-session with /model), plus the context-window suffix from
# the live process's argv, which is the ONLY place `[1m]` survives (the API never
# echoes it back, so a transcript-only recovery silently downgrades a 1M session
# to the default window on resume). Empty if neither source knows.
recover_model() {
  local sid="$1" cpid="${2:-}" pr tf model cm
  for pr in "${CONFIG_DIRS[@]/%//projects}"; do
    tf=$(find -L "$pr" -maxdepth 2 -name "$sid.jsonl" -type f 2>/dev/null | head -1)
    [[ -n "$tf" ]] && break
  done
  if [[ -n "${tf:-}" ]]; then
    model=$(grep -a '"type":"assistant"' "$tf" 2>/dev/null \
            | jq -r 'select(.isSidechain==false) | .message.model // empty' 2>/dev/null \
            | grep -v '^null$' | tail -1)
  fi
  cm=$(rr_proc_model "$cpid")
  if [[ -z "${model:-}" ]]; then
    model="$cm"                                   # transcript unusable -> take argv whole
  else
    model="${model}$(rr_model_suffix "$cm")"      # transcript base + live suffix
  fi
  echo "$model"
}

# Per-pane checkpoint note. The preferred key is the tmux PANE id: it is stable
# for the life of a pane and immune to session-id churn. A note keyed only by
# session id is orphaned if the session's id changes before the final snapshot,
# which is how two undeliverable notes ended up on disk. The session-id path is
# the legacy location and is still honored.
PANE_NOTES_DIR="${RR_PANE_NOTES_DIR:-${RR_SELFREG_DIR:+${RR_SELFREG_DIR%/panes}/notes}}"
NOTE=""; NOTE_SRC=""
# Sets the globals $NOTE and $NOTE_SRC. Deliberately not "echo the note and
# capture it" -- a command substitution runs in a subshell, so the source path
# assigned inside would be lost and the delivered note would never be cleaned up.
read_note() {  # <pane_id> <session_id>
  local paneid="${1:-}" sid="${2:-}" p k
  NOTE=""; NOTE_SRC=""
  k=$(printf '%s' "$paneid" | tr -c 'A-Za-z0-9._-' '_')
  for p in "${PANE_NOTES_DIR:+$PANE_NOTES_DIR/$k.txt}" "${sid:+$NOTES_DIR/$sid.txt}"; do
    [[ -n "$p" && -f "$p" ]] || continue
    NOTE=$(cat "$p"); NOTE_SRC="$p"; return 0
  done
  return 0
}

# Per-pane session jump of the open-science core (optional; see rr_common.sh
# rr_jump_find and reference/jump-hook.md). A jump's worker, and the background
# Bash that wakes a session after a wait jump, are processes inside this SLURM
# job, so both die at the wall-clock limit. Capturing the record here lets
# rr_deliver.sh finish the jump, or wake the waiting pane, in the successor job.
# The record gains rr_core_scripts: where the core's jump.sh was found for this
# pane (empty if the core is not installed). Sets $JUMP (compact JSON or "null")
# and $JUMP_SRC.
JUMP="null"; JUMP_SRC=""
read_jump() {  # <pane_id> <claude_pid> <session_id> <config_dir>
  local rec core
  JUMP="null"; JUMP_SRC=""
  rec=$(rr_jump_find "$SOCK" "$1" "$2" "$3" "$4")
  [[ -n "$rec" ]] || return 0
  core=$(rr_core_scripts "$2" "$4") || core=""
  JUMP=$(jq -c --arg c "$core" '. + {rr_core_scripts:$c}' <<<"$rec")
  JUMP_SRC=$(jq -r '.rr_src // ""' <<<"$rec")
  return 0
}

# --- walk windows and panes --------------------------------------------------
win_objs=()
while IFS=$'\t' read -r widx wname wactive wlayout; do
  [[ -n "$widx" ]] || continue
  pane_objs=()
  while IFS=$'\t' read -r pidx pcwd ppid paneid; do
    [[ -n "$pidx" ]] || continue
    # A pane is a Claude pane IFF a live claude process runs under it. Nothing
    # stored on disk can promote a pane to Claude-ness -- so a session that was
    # exited (`exit`, Ctrl-C) or whose pane was closed is simply not captured,
    # and can never be resurrected as a zombie or inherited by a neighbour.
    if info=$(rr_resolve_claude "$ppid" "$paneid"); then
      IFS='|' read -r cdir cpid sid status <<< "$info"
      model=$(recover_model "$sid" "$cpid")
      # CLAUDE_CONFIG_DIR as the live process had it (empty = it used the
      # default). The rebuild sets the same value, so the session resumes from
      # the config dir that holds its transcript, whatever the launch command is.
      cdir_env=$(rr_proc_env "$cpid" CLAUDE_CONFIG_DIR)
      rcname=$(rr_rc_name "$cdir" "$cpid" "$SESSION" "$widx" "$pidx")
      read_note "$paneid" "$sid"; note="$NOTE"; nsrc="$NOTE_SRC"
      read_jump "$paneid" "$cpid" "$sid" "$cdir"
      # A pane should never carry both. If it does, the jump is the live intent
      # (a note is written at wind-down, when jumping is already forbidden), so
      # the jump wins and the note is set aside rather than deleted or delivered
      # on top of it -- losing an agent's own self-message is not acceptable,
      # and delivering two instructions into one prompt is worse.
      if [[ "$JUMP" != "null" && -n "$note" ]]; then
        [[ -n "$nsrc" ]] && mv "$nsrc" "$nsrc.superseded" 2>/dev/null
        echo "rr_snapshot: pane $paneid has both a note and a pending jump; jump wins, note moved to ${nsrc:-?}.superseded" >&2
        note=""; nsrc=""
      fi
      pane_objs+=("$(jq -n \
        --argjson idx "$pidx" --arg cwd "$pcwd" --arg pane "$paneid" \
        --arg sid "$sid" --arg cdir "$cdir" --arg cdenv "$cdir_env" \
        --arg model "$model" --arg status "$status" --arg rcname "$rcname" \
        --arg note "$note" --arg nsrc "$nsrc" \
        --argjson jump "$JUMP" --arg jsrc "$JUMP_SRC" \
        '{index:$idx, cwd:$cwd, claude:true, session_id:$sid, pane_id:$pane,
          config_dir:$cdir, config_dir_env:$cdenv, model:$model, status:$status,
          rc_name:$rcname,
          note:$note, note_src:$nsrc, jump:$jump, jump_src:$jsrc}')")
    else
      # No live Claude here -> forget any mapping we had for this pane, so it can
      # never be applied to a different session started in the pane later.
      rr_pane_cache_drop "$paneid"
      pane_objs+=("$(jq -n --argjson idx "$pidx" --arg cwd "$pcwd" \
        '{index:$idx, cwd:$cwd, claude:false}')")
    fi
  done < <("${T[@]}" list-panes -t "$SESSION:$widx" \
             -F $'#{pane_index}\t#{pane_current_path}\t#{pane_pid}\t#{pane_id}')

  if [[ ${#pane_objs[@]} -gt 0 ]]; then
    panes_json=$(printf '%s\n' "${pane_objs[@]}" | jq -s 'sort_by(.index)')
  else
    panes_json='[]'
  fi
  win_objs+=("$(jq -n --argjson idx "$widx" --arg name "$wname" \
     --argjson active "${wactive:-0}" --arg layout "$wlayout" \
     --argjson panes "$panes_json" \
     '{index:$idx, name:$name, active:($active==1), layout:$layout, panes:$panes}')")
done < <("${T[@]}" list-windows -t "$SESSION" \
           -F $'#{window_index}\t#{window_name}\t#{window_active}\t#{window_layout}')

if [[ ${#win_objs[@]} -gt 0 ]]; then
  windows_json=$(printf '%s\n' "${win_objs[@]}" | jq -s 'sort_by(.index)')
else
  windows_json='[]'
fi

# Base indices (default 0) so rebuild reproduces the exact window/pane numbering
# the layout strings and pane targets assume.
base_index=$("${T[@]}" show-options -gv base-index 2>/dev/null); base_index=${base_index:-0}
pane_base_index=$("${T[@]}" show-options -gv pane-base-index 2>/dev/null); pane_base_index=${pane_base_index:-0}

jq -n --arg name "$SESSION" --arg sock "$SOCK" --argjson windows "$windows_json" \
  --argjson bi "$base_index" --argjson pbi "$pane_base_index" \
  '{session_name:$name, tmux_socket:$sock,
    base_index:$bi, pane_base_index:$pbi, windows:$windows,
    snapshot_at:(now|todate)}' > "$OUT"
