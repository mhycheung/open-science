#!/bin/bash
# Usage: rr_rebuild.sh <snapshot.json> <target_tmux_socket> [registry_record.json]
#
# Recreate one tmux session from a snapshot produced by rr_snapshot.sh on the
# TARGET socket: same windows, same panes (in index order), exact geometry via
# `select-layout`, each pane in its original cwd. Then, in every pane that was
# running Claude, start the configured launch command (rr_launch_cmd, default
# `claude`) resuming the exact session with the same model and config dir, and
# with the permission mode and Remote Control setting the user chose when
# registering (read from the registry record; defaults bypassPermissions / on).
#
# It does NOT wait for boot or send continuation prompts -- it prints a JSONL
# manifest of the Claude panes it launched, one object per line:
#     {socket, target, status, session_id, launch, permission_mode,
#      remote_control, rc_name, note, note_src, jump, jump_src}
# so the caller (rr_deliver.sh) can do the collective post-boot steps across ALL
# rebuilt sessions at once. The checkpoint note travels IN the manifest, resolved
# by the snapshot while the session id was still current.
#
# Set RR_REBUILD_DRYRUN=1 to build the layout only and send the real launch
# command COMMENTED OUT (`# claude --model ... --resume ...`) instead of
# running it -- for testing on a scratch socket without spawning Claude.
set -u
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/rr_common.sh"

SNAP="${1:?snapshot json required}"
SOCK="${2:?target tmux socket path required}"
REC="${3:-}"
# The user's choices at registration, carried hop to hop in the registry record.
PERM="bypassPermissions"; RC="true"
if [[ -n "$REC" && -f "$REC" ]]; then
  PERM=$(jq -r '.permission_mode // "bypassPermissions"' "$REC")
  RC=$(jq -r 'if .remote_control == false then "false" else "true" end' "$REC")
fi
LAUNCH=$(rr_launch_cmd)
D=(tmux -S "$SOCK")

# tmux does NOT auto-create the parent dir for an explicit -S socket path (unlike
# -L). On a fresh node the socket directory (tmux-<uid> under /tmp) may not exist, so create it (mode 700, as
# tmux requires) before starting the server.
_sockdir=$(dirname "$SOCK")
mkdir -p "$_sockdir" 2>/dev/null && chmod 700 "$_sockdir" 2>/dev/null || true

# Session name comes from the snapshot, so the resurrected session answers to the
# same `tmux attach -t <name>` the user typed before. RR_REBUILD_NAME overrides it
# for the rare case where that name is already taken on the target socket.
name="${RR_REBUILD_NAME:-$(jq -r '.session_name' "$SNAP")}"
bi=$(jq -r '.base_index // 0' "$SNAP")
pbi=$(jq -r '.pane_base_index // 0' "$SNAP")
nwin=$(jq -r '.windows | length' "$SNAP")

# Make the detached server number windows/panes exactly as the source did, so
# the layout strings and `session:win.pane` targets line up.
"${D[@]}" set-option -g base-index "$bi" >/dev/null 2>&1
"${D[@]}" set-option -g pane-base-index "$pbi" >/dev/null 2>&1

launch_cmd() {  # emit the shell command to start claude in a pane
  local cdenv="$1" model="$2" sid="$3" rcname="${4:-}" env="" cmd
  # Fill in a missing context-window suffix per config.context_window_suffix
  # (default: none). A suffix already recorded in the snapshot is left alone.
  model=$(rr_apply_model_policy "$model")
  # Restore the process's CLAUDE_CONFIG_DIR: the transcript lives under it, so
  # `--resume` only finds the conversation with the same config dir.
  [[ -n "$cdenv" && "$cdenv" != "null" ]] && env="CLAUDE_CONFIG_DIR=\"$cdenv\" "
  # Remote Control name. It goes in the environment, not argv: a wrapper that
  # already passes `--remote-control ""` wins over a later argv name (first flag
  # wins), while the env var composes with it. The same pane keeps the same name
  # across every hop.
  if [[ "$RC" == "true" && -n "$rcname" && "$rcname" != "null" ]]; then
    env="${env}CLAUDE_CODE_SESSION_NAME=\"$rcname\" "
  fi
  cmd="${env}${LAUNCH}"
  # Quote model so a context-window suffix like [1m] survives globbing. Omit
  # --model entirely when unknown (resume keeps the default).
  [[ -n "$model" && "$model" != "null" ]] && cmd="$cmd --model \"$model\""
  cmd="$cmd --resume \"$sid\""
  [[ -n "$PERM" && "$PERM" != "null" ]] && cmd="$cmd --permission-mode $PERM"
  [[ "$RC" == "true" ]] && cmd="$cmd --remote-control"
  printf '%s' "$cmd"
}

created_session=0
for ((w=0; w<nwin; w++)); do
  widx=$(jq -r ".windows[$w].index" "$SNAP")
  wname=$(jq -r ".windows[$w].name" "$SNAP")
  layout=$(jq -r ".windows[$w].layout" "$SNAP")
  npane=$(jq -r ".windows[$w].panes | length" "$SNAP")
  cwd0=$(jq -r ".windows[$w].panes[0].cwd" "$SNAP")
  # Detached size from the layout's overall WxH so splits have room and
  # select-layout reproduces the geometry exactly.
  dims=$(sed -E 's/^[^,]+,([0-9]+x[0-9]+).*/\1/' <<< "$layout")
  cols=${dims%x*}; rows=${dims#*x}
  [[ "$cols" =~ ^[0-9]+$ ]] || cols=210; [[ "$rows" =~ ^[0-9]+$ ]] || rows=56

  if [[ $created_session -eq 0 ]]; then
    "${D[@]}" new-session -d -s "$name" -n "$wname" -c "$cwd0" -x "$cols" -y "$rows"
    # Move the initial window to the source's first window index if needed.
    firstidx=$("${D[@]}" list-windows -t "$name" -F '#{window_index}' | head -1)
    [[ "$firstidx" == "$widx" ]] || "${D[@]}" move-window -s "$name:$firstidx" -t "$name:$widx"
    created_session=1
  else
    "${D[@]}" new-window -d -t "$name:$widx" -n "$wname" -c "$cwd0"
  fi

  # Create the remaining panes. Re-tile after each split so no pane shrinks below
  # tmux's minimum -- otherwise split-window fails with "no space for new pane"
  # once a nested layout has many panes, silently dropping panes (and their
  # Claude sessions). The exact geometry is restored by select-layout at the end;
  # the intermediate tiling only guarantees room to keep splitting.
  #
  # Positions are preserved by index: tmux emits a window_layout string with its
  # leaves in pane-INDEX order, and select-layout assigns the window's panes (also
  # index order) to those leaves in turn. So freshly-created pane index i lands at
  # exactly source pane index i's screen position (verified: per-index left/top/WxH
  # match the source). The second pass therefore targets panes by source index.
  for ((p=1; p<npane; p++)); do
    pcwd=$(jq -r ".windows[$w].panes[$p].cwd" "$SNAP")
    if ! "${D[@]}" split-window -t "$name:$widx" -c "$pcwd" >/dev/null 2>&1; then
      "${D[@]}" select-layout -t "$name:$widx" tiled >/dev/null 2>&1
      "${D[@]}" split-window -t "$name:$widx" -c "$pcwd" >/dev/null 2>&1
    fi
    "${D[@]}" select-layout -t "$name:$widx" tiled >/dev/null 2>&1
  done
  "${D[@]}" select-layout -t "$name:$widx" "$layout" >/dev/null 2>&1
  built=$("${D[@]}" list-panes -t "$name:$widx" 2>/dev/null | wc -l)
  [[ "$built" -eq "$npane" ]] || \
    echo "rr_rebuild WARNING: window $widx wanted $npane panes, built $built" >&2
done

# Second pass: launch Claude in the panes that had it, and emit the manifest.
for ((w=0; w<nwin; w++)); do
  widx=$(jq -r ".windows[$w].index" "$SNAP")
  npane=$(jq -r ".windows[$w].panes | length" "$SNAP")
  for ((p=0; p<npane; p++)); do
    is=$(jq -r ".windows[$w].panes[$p].claude" "$SNAP")
    [[ "$is" == "true" ]] || continue
    pidx=$(jq -r ".windows[$w].panes[$p].index" "$SNAP")
    sid=$(jq -r ".windows[$w].panes[$p].session_id" "$SNAP")
    status=$(jq -r ".windows[$w].panes[$p].status" "$SNAP")
    cdenv=$(jq -r ".windows[$w].panes[$p].config_dir_env // \"\"" "$SNAP")
    model=$(jq -r ".windows[$w].panes[$p].model" "$SNAP")
    cdir=$(jq -r ".windows[$w].panes[$p].config_dir" "$SNAP")
    note=$(jq -r ".windows[$w].panes[$p].note // \"\"" "$SNAP")
    nsrc=$(jq -r ".windows[$w].panes[$p].note_src // \"\"" "$SNAP")
    # A session jump interrupted by the wall-clock limit. Travels in the manifest
    # exactly as the note does, so rr_deliver.sh can finish or re-arm it here.
    jump=$(jq -c ".windows[$w].panes[$p].jump // null" "$SNAP")
    jsrc=$(jq -r ".windows[$w].panes[$p].jump_src // \"\"" "$SNAP")
    # Stable Remote Control name. Snapshots written before 2026-08-18 have no
    # rc_name; mint the same pane-derived name the snapshot would have.
    rcname=$(jq -r ".windows[$w].panes[$p].rc_name // \"\"" "$SNAP")
    [[ -n "$rcname" && "$rcname" != "null" ]] || rcname=$(rr_rc_name_for_pane "$name" "$widx" "$pidx")
    target="$name:$widx.$pidx"
    lc=$(launch_cmd "$cdenv" "$model" "$sid" "$rcname")
    if [[ "${RR_REBUILD_DRYRUN:-0}" == "1" ]]; then
      "${D[@]}" send-keys -t "$target" "# $lc" Enter
    else
      "${D[@]}" send-keys -t "$target" "$lc" Enter
    fi
    jq -nc --arg sock "$SOCK" --arg target "$target" --arg status "$status" \
           --arg sid "$sid" --arg note "$note" --arg nsrc "$nsrc" \
           --arg rcname "$rcname" --arg launch "$lc" --arg perm "$PERM" \
           --argjson rc "$RC" \
           --argjson jump "${jump:-null}" --arg jsrc "$jsrc" \
      '{socket:$sock, target:$target, status:$status, session_id:$sid,
        launch:$launch, permission_mode:$perm, remote_control:$rc,
        rc_name:$rcname, note:$note, note_src:$nsrc, jump:$jump, jump_src:$jsrc}'
    # Carry-forward self-registration: record this session under its NEW pane id so
    # the next hop's snapshot is authoritative even if the pane goes idle and its
    # state file is later deleted by a cross-node cleanup. (Idle-with-no-child panes
    # are otherwise undiscoverable passively -- exactly what self-reg exists to fix.)
    if [[ -n "${RR_WRITE_SELFREG_DIR:-}" ]]; then
      newpid=$("${D[@]}" display-message -p -t "$target" '#{pane_id}' 2>/dev/null)
      if [[ -n "$newpid" ]]; then
        mkdir -p "$RR_WRITE_SELFREG_DIR"
        san=$(printf '%s' "$newpid" | tr -c 'A-Za-z0-9._-' '_')
        jq -n --arg sid "$sid" --arg cdir "$cdir" \
              --arg pid "$newpid" --arg sock "$SOCK" --arg sess "$name" \
              --arg win "$widx" --arg pidx "$pidx" \
          '{session_id:$sid, config_dir:$cdir, pane_id:$pid,
            tmux_socket:$sock, tmux_session:$sess,
            window_index:($win|tonumber), pane_index:($pidx|tonumber),
            registered_at:(now|todate)}' > "$RR_WRITE_SELFREG_DIR/$san.json"
      fi
    fi
  done
done
