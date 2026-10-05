#!/bin/bash
# Usage: rr_rebuild.sh <snapshot.json> <target_tmux_socket> [registry_record.json]
#
# Recreate one tmux session from a snapshot produced by rr_snapshot.sh on the
# TARGET socket: same windows, same panes (in index order), exact geometry via
# `select-layout`, each pane in its original cwd. Then, in every pane that was
# running Claude, start the configured launch command (rr_launch_cmd, default
# `claude`) resuming the exact session with the same model and config dir, the
# pane's OWN permission mode (read from its argv by the snapshot), and the Remote
# Control setting the user chose when registering (from the registry record).
#
# Permission mode of each resumed Claude pane, never wider than before:
#   * the snapshot read the mode the pane's process was started with: resume with
#     that mode, or with the registered mode if that is narrower (the registered
#     mode is an upper bound). A pane started without a mode flag is resumed
#     without one, so Claude Code's own settings decide, as they did before.
#   * the mode could not be read: the registered mode if the user chose one
#     explicitly, else no mode flag.
#   * no registry record: at most `manual`, and Remote Control off.
#
# Panes that ran Codex are resumed with `codex resume` (launch_codex below).
#
# It does NOT wait for boot or send continuation prompts -- it prints a JSONL
# manifest of the Claude panes it launched, one object per line:
#     {socket, target, status, session_id, launch, permission_mode,
#      remote_control, rc_name, note, note_src, jump, jump_src}
# so the caller (rr_deliver.sh) can do the collective post-boot steps across ALL
# rebuilt sessions at once. The checkpoint note travels IN the manifest, resolved
# by the snapshot while the session id was still current.
#
# Every value taken from the snapshot is quoted with printf %q before it is typed
# into the pane's shell, and a Claude session id must be a UUID.
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
# CAP: upper bound on the permission mode ("" = none); EXPLICIT: the user chose it,
# so it is also used for panes whose own mode is unknown. A record written before
# permission_mode_explicit existed counts as explicit unless it holds the old
# default, bypassPermissions.
NOREC=1; CAP="manual"; EXPLICIT=false; RC="false"
if [[ -n "$REC" && -f "$REC" ]]; then
  NOREC=0
  CAP=$(jq -r '.permission_mode // ""' "$REC")
  EXPLICIT=$(jq -r 'if .permission_mode_explicit != null then (.permission_mode_explicit == true)
                    else (.permission_mode != null and .permission_mode != "bypassPermissions") end' "$REC")
  RC=$(jq -r 'if .remote_control == false then "false" else "true" end' "$REC")
  if [[ -n "$CAP" ]] && ! rr_perm_rank "$CAP" >/dev/null; then
    echo "rr_rebuild WARNING: unknown permission mode '$CAP' in $REC; using manual" >&2
    CAP="manual"; EXPLICIT=true
  fi
fi
LAUNCH=$(rr_launch_cmd)
D=(tmux -S "$SOCK")

# tmux does NOT auto-create the parent dir for an explicit -S socket path (unlike
# -L), and does not check it either. Create a missing directory with mode 700, then
# refuse to start a server unless the directory is private to this user
# (rr_sock_ok; rr_successor.sh has already picked a safe path with rr_safe_sock).
_sockdir=$(dirname "$SOCK")
[[ -e "$_sockdir" || -L "$_sockdir" ]] || mkdir -m 700 "$_sockdir" 2>/dev/null
if ! rr_sock_ok "$SOCK"; then
  echo "rr_rebuild: refusing to start a tmux server on $SOCK" >&2
  exit 1
fi

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

# The permission flags for one Claude pane (see the header). Sets PERM (a mode, or
# "" for no flag) and ALLOWB (1: pass --allow-dangerously-skip-permissions).
pane_perm() {  # <known true|false> <mode> <allow_bypass true|false>
  local known="$1" mode="$2" allow="$3"
  PERM=""; ALLOWB=0
  if [[ "$known" == true ]] && { [[ -z "$mode" ]] || rr_perm_rank "$mode" >/dev/null; }; then
    PERM="$mode"
    if [[ -n "$PERM" && -n "$CAP" ]] && (( $(rr_perm_rank "$PERM") > $(rr_perm_rank "$CAP") )); then PERM="$CAP"; fi
    [[ "$allow" == true && $NOREC -eq 0 && ( -z "$CAP" || "$CAP" == bypassPermissions ) ]] && ALLOWB=1
  elif [[ $NOREC -eq 0 && "$EXPLICIT" == true ]]; then
    PERM="$CAP"
  fi
  return 0
}

launch_cmd() {  # emit the shell command to start claude in a pane
  local cdenv="$1" model="$2" sid="$3" rcname="${4:-}" env="" cmd
  # Fill in a missing context-window suffix per config.context_window_suffix
  # (default: none). A suffix already recorded in the snapshot is left alone.
  model=$(rr_apply_model_policy "$model")
  # Restore the process's CLAUDE_CONFIG_DIR: the transcript lives under it, so
  # `--resume` only finds the conversation with the same config dir.
  [[ -n "$cdenv" && "$cdenv" != "null" ]] && env="CLAUDE_CONFIG_DIR=$(printf '%q' "$cdenv") "
  # Remote Control name. It goes in the environment, not argv: a wrapper that
  # already passes `--remote-control ""` wins over a later argv name (first flag
  # wins), while the env var composes with it. The same pane keeps the same name
  # across every hop. The name can be set by the user, so it is quoted.
  if [[ "$RC" == "true" && -n "$rcname" && "$rcname" != "null" ]]; then
    env="${env}CLAUDE_CODE_SESSION_NAME=$(printf '%q' "$rcname") "
  fi
  cmd="${env}${LAUNCH}"
  # Omit --model entirely when unknown (resume keeps the default).
  [[ -n "$model" && "$model" != "null" ]] && cmd="$cmd --model $(printf '%q' "$model")"
  cmd="$cmd --resume $(printf '%q' "$sid")"
  [[ -n "$PERM" ]] && cmd="$cmd --permission-mode $(printf '%q' "$PERM")"
  [[ "$ALLOWB" == 1 ]] && cmd="$cmd --allow-dangerously-skip-permissions"
  [[ "$RC" == "true" ]] && cmd="$cmd --remote-control"
  printf '%s' "$cmd"
}

# A Codex pane: `[CODEX_HOME=..] codex resume <options> [-m model] <thread>`, typed into
# the pane's shell. The options are the old TUI's own (rr_codex_options): sandbox,
# approval policy, profile, config overrides, exactly as the user started it. The
# registry's permission mode is a Claude Code setting and is NOT applied to Codex, and
# nothing that widens permissions is ever added. A pane whose thread or options were
# not known is left a plain shell and reported in the manifest (status unresumable).
launch_codex() {  # <window array idx> <pane array idx>
  local w="$1" p="$2" pj widx pidx target tid home model why lc o newpid san
  pj=$(jq -c ".windows[$w].panes[$p]" "$SNAP")
  widx=$(jq -r ".windows[$w].index" "$SNAP"); pidx=$(jq -r '.index' <<<"$pj")
  target="$name:$widx.$pidx"
  tid=$(jq -r '.session_id // ""' <<<"$pj"); home=$(jq -r '.codex_home // ""' <<<"$pj")
  model=$(jq -r '.model // ""' <<<"$pj"); why=$(jq -r '.codex_unresumable // ""' <<<"$pj")
  # A thread id is a UUID; anything outside [A-Za-z0-9._-] is not typed into a shell.
  [[ -z "$tid" || "$tid" =~ ^[A-Za-z0-9._-]+$ ]] || { why="thread id '$tid' has unexpected characters"; tid=""; }
  if [[ "$(jq -r '.codex' <<<"$pj")" != true || -z "$tid" ]]; then
    jq -nc --arg sock "$SOCK" --arg target "$target" --arg why "${why:-not resumable}" \
      '{socket:$sock, target:$target, runtime:"codex", status:"unresumable", reason:$why, session_id:""}'
    return 0
  fi
  lc=""
  [[ -n "$home" ]] && lc="CODEX_HOME=$(printf '%q' "$home") "
  lc="${lc}$(rr_codex_launch_cmd) resume"
  while IFS= read -r -d '' o; do lc="$lc $(printf '%q' "$o")"; done \
    < <(jq -j '.codex_options[]? | (. + "\u0000")' <<<"$pj")
  [[ -n "$model" ]] && lc="$lc -m $(printf '%q' "$model")"
  lc="$lc $(printf '%q' "$tid")"
  if [[ "${RR_REBUILD_DRYRUN:-0}" == "1" ]]; then "${D[@]}" send-keys -t "$target" "# $lc" Enter
  else "${D[@]}" send-keys -t "$target" "$lc" Enter; fi
  jq -nc --arg sock "$SOCK" --arg target "$target" --arg status "$(jq -r '.status // ""' <<<"$pj")" \
     --arg sid "$tid" --arg home "$home" --arg launch "$lc" \
     --arg note "$(jq -r '.note // ""' <<<"$pj")" --arg nsrc "$(jq -r '.note_src // ""' <<<"$pj")" \
    '{socket:$sock, target:$target, runtime:"codex", status:$status, session_id:$sid,
      codex_home:$home, launch:$launch, note:$note, note_src:$nsrc, jump:null}'
  # Seed the next hop's pane cache: a resumed Codex runs no hook before its first prompt.
  if [[ -n "${RR_WRITE_SELFREG_DIR:-}" ]]; then
    newpid=$("${D[@]}" display-message -p -t "$target" '#{pane_id}' 2>/dev/null)
    if [[ -n "$newpid" ]]; then
      mkdir -p "$RR_WRITE_SELFREG_DIR"; san=$(printf '%s' "$newpid" | tr -c 'A-Za-z0-9._-' '_')
      jq -n --arg sid "$tid" --arg h "$home" --arg m "$model" --arg pid "$newpid" \
        '{runtime:"codex", session_id:$sid, codex_home:$h, model:$m, pane_id:$pid, registered_at:(now|todate)}' \
        | rr_write "$RR_WRITE_SELFREG_DIR/$san.json"
    fi
  fi
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
    if [[ "$(jq -r ".windows[$w].panes[$p].runtime // \"claude\"" "$SNAP")" == codex ]]; then
      launch_codex "$w" "$p"; continue
    fi
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
    if ! rr_valid_uuid "$sid"; then
      echo "rr_rebuild WARNING: $target: session id '$sid' is not a UUID; Claude not resumed there" >&2
      jq -nc --arg sock "$SOCK" --arg target "$target" --arg sid "$sid" \
        '{socket:$sock, target:$target, runtime:"claude", status:"unresumable",
          reason:"session id is not a UUID", session_id:$sid, note:"", jump:null}'
      continue
    fi
    pane_perm "$(jq -r ".windows[$w].panes[$p].permission_mode_known // false" "$SNAP")" \
              "$(jq -r ".windows[$w].panes[$p].permission_mode // \"\"" "$SNAP")" \
              "$(jq -r ".windows[$w].panes[$p].allow_bypass // false" "$SNAP")"
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
            registered_at:(now|todate)}' | rr_write "$RR_WRITE_SELFREG_DIR/$san.json"
      fi
    fi
  done
done
