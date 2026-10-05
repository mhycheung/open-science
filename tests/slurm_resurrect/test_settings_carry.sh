#!/bin/bash
# opsci: planted-leaks (fake SLURM job ids for the stub scheduler)
# The user's choices at registration and each pane's own settings reach the resumed
# session: permission mode (the pane's own, never wider, at most the registered one),
# Remote Control, the Claude process's CLAUDE_CONFIG_DIR, and the launch command.
#
#   bash tests/slurm_resurrect/test_settings_carry.sh
#
# A registered session holding a fake Claude pane is rebuilt by rr_successor.sh.
# The launch command is a recorder that writes its argv and environment to a
# file, so the test sees exactly what a real `claude` would have been given. The
# recorder then becomes a process named `claude`, so the rebuilt pane counts as a
# live agent.
#
# Also covered: values from the snapshot cannot run shell code when the launch
# command is typed; a socket directory other users can reach is not used; a
# successor that resumed no agent ends the lineage.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
rr_test_setup
RR="$SCRIPTS/rr_registry.sh"
job 424242 RUNNING 1:00:00 2:00:00

cat > "$TMP/bin/recorder" <<REC
#!/bin/bash
out="$TMP/launch.\$\$"
{ echo "CCD=\${CLAUDE_CONFIG_DIR:-}"; echo "NAME=\${CLAUDE_CODE_SESSION_NAME:-}"
  printf 'ARG=%s\n' "\$@"; } > "\$out.tmp" && mv "\$out.tmp" "\$out"
exec $TMP/bin/claude 600
REC
chmod +x "$TMP/bin/recorder"

uuid() { printf '00000000-0000-4000-8000-%012d' "$1"; }

# one_hop <state dir> <session> <fake claude args> [register options...]: start a
# fake Claude with those arguments, register, rebuild, and set $LAUNCH_OUT to the
# recorder output for the rebuilt Claude pane. Set $EDIT_SNAP to a jq filter to
# change the snapshot before the successor reads it.
N=0
one_hop() {
  local st="$1" sess="$2" fargs="$3"; shift 3
  N=$((N+1)); SID=$(uuid "$N")
  export RR_STATE_DIR="$st"; mkdir -p "$st"; touch "$st/warning_shown"
  export SLURM_JOB_ID=424242
  start_tmux "$sess"
  FAKE_CLAUDE_ARGS="$fargs" fake_claude "$sess:0.0" "$SID" "claude-test-model" >/dev/null
  tmux -S "$SOCK" new-window -d -t "$sess:1"
  in_pane "$sess:1" "bash $RR register $*" > "$TMP/reg.$sess.out"
  REG_RC=$(pane_rc)
  in_pane "$sess:1" "bash $RR set launch_cmd ${LAUNCH_CMD:-$TMP/bin/recorder}" >/dev/null
  if [[ -n "${EDIT_SNAP:-}" ]]; then
    local sf="$st/registry/424242/snapshot/$sess.json"
    jq "$EDIT_SNAP" "$sf" > "$sf.tmp" && mv "$sf.tmp" "$sf"
  fi
  job 424242 COMPLETED            # the source job has ended: no handoff
  rm -f "$STUB/jobs/424242"
  rm -f "$TMP"/launch.*
  SLURM_JOB_ID=424243 RR_DELIVER_DRYRUN=1 RR_NO_COORDINATOR=1 RR_SUCCESSOR_NO_HOLD=1 \
    RR_ALIVE_WAIT="${ALIVE_WAIT:-30}" TMPDIR="$TMP" XDG_RUNTIME_DIR= \
    bash "$SCRIPTS/rr_successor.sh" 424242 > "$TMP/succ.$sess.out" 2>&1
  local i; for ((i=0; i<50; i++)); do ls "$TMP"/launch.* >/dev/null 2>&1 && break; sleep 0.2; done
  LAUNCH_OUT=$(cat "$TMP"/launch.* 2>/dev/null)
  job 424242 RUNNING 1:00:00 2:00:00
}

echo "== 1. pane in bypassPermissions, registered at most acceptEdits, Remote Control off =="
one_hop "$TMP/rrA" alpha "--permission-mode bypassPermissions" --permission-mode acceptEdits --remote-control off
out="$LAUNCH_OUT"
check "register accepted" "$REG_RC" "0"
has   "resumes the recorded session"            "$out" $'ARG=--resume\nARG='"$SID"
has   "same model"                              "$out" $'ARG=--model\nARG=claude-test-model'
has   "mode capped at the registered acceptEdits" "$out" $'ARG=--permission-mode\nARG=acceptEdits'
hasnt "no bypassPermissions"                    "$out" "bypassPermissions"
hasnt "Remote Control off -> no --remote-control" "$out" "ARG=--remote-control"
has   "Remote Control off -> no session name"   "$out" $'NAME=\n'
has   "CLAUDE_CONFIG_DIR restored from the process" "$out" "CCD=$TMP/cfg"
CARRIED="$TMP/rrA/registry/424243/alpha.json"
check "carried record keeps the permission mode" "$(jq -r .permission_mode "$CARRIED" 2>/dev/null)" "acceptEdits"
check "carried record keeps Remote Control off"  "$(jq -r .remote_control "$CARRIED" 2>/dev/null)" "false"
check "manifest records the permission mode" \
  "$(jq -r .permission_mode "$TMP/rrA/rr_manifest_424243.jsonl" 2>/dev/null)" "acceptEdits"

echo "== 2. pane started with --dangerously-skip-permissions, default registration =="
one_hop "$TMP/rrB" beta "--dangerously-skip-permissions"; out="$LAUNCH_OUT"
check "register accepted" "$REG_RC" "0"
has   "the pane's own bypass mode is kept" "$out" $'ARG=--permission-mode\nARG=bypassPermissions'
has   "Remote Control on -> --remote-control"   "$out" "ARG=--remote-control"
[[ "$out" =~ NAME=([^$'\n']+) ]] && ok "Remote Control on -> session name set (${BASH_REMATCH[1]})" \
  || bad "session name must be set" "$out"
check "carried record: Remote Control on" "$(jq -r .remote_control "$TMP/rrB/registry/424243/beta.json" 2>/dev/null)" "true"

echo "== 3. pane started without a mode flag, default registration: no mode flag =="
one_hop "$TMP/rrC" gamma "--verbose"; out="$LAUNCH_OUT"
has   "resumed" "$out" $'ARG=--resume\nARG='"$SID"
hasnt "no --permission-mode added" "$out" "ARG=--permission-mode"
hasnt "never bypassPermissions" "$out" "bypassPermissions"

echo "== 4. pane in plan, registered at most bypassPermissions: plan (never wider) =="
one_hop "$TMP/rrD" delta "--permission-mode plan" --permission-mode bypassPermissions; out="$LAUNCH_OUT"
has   "the pane's narrower mode is kept" "$out" $'ARG=--permission-mode\nARG=plan'
hasnt "not widened to the registered mode" "$out" "bypassPermissions"

echo "== 5. invalid choices are refused, nothing registered =="
export RR_STATE_DIR="$TMP/rrE"; mkdir -p "$RR_STATE_DIR"; touch "$RR_STATE_DIR/warning_shown"
out=$(in_pane beta:1 "bash $RR register --permission-mode yolo")
check "unknown permission mode -> exit 2" "$(pane_rc)" "2"
out=$(in_pane beta:1 "bash $RR register --remote-control maybe")
check "bad --remote-control value -> exit 2" "$(pane_rc)" "2"
[[ ! -e "$RR_STATE_DIR/registry/424242/beta.json" ]] && ok "nothing registered" || bad "nothing registered" "record exists"
out=$(in_pane beta:1 "bash $RR set auto_trust maybe")
check "set auto_trust maybe -> exit 2" "$(pane_rc)" "2"
out=$(in_pane beta:1 "bash $RR set auto_trust true")
check "set auto_trust true (the user, in a terminal)" "$(jq -r .auto_trust "$RR_STATE_DIR/rr_config.json")" "true"
CLAUDECODE=1 bash "$RR" set auto_trust false >/dev/null 2>&1
check "an agent cannot change auto_trust (exit 3)" "$?" "3"

echo "== 6. rebuild alone: missing record, unreadable mode =="
# A snapshot whose one Claude pane ran in bypassPermissions (or whose mode is unknown).
mksnap() {  # <known true|false> <mode>
  jq -n --arg sock "$SOCK" --arg sid "$(uuid 99)" --arg cwd "$TMP" --argjson known "$1" --arg mode "$2" \
    '{session_name:"solo", tmux_socket:$sock, base_index:0, pane_base_index:0,
      windows:[{index:0, name:"w", active:true, layout:"",
        panes:[{index:0, cwd:$cwd, claude:true, session_id:$sid, config_dir:"", config_dir_env:"",
                model:"m", status:"idle", rc_name:"solo-name", permission_mode_known:$known,
                permission_mode:$mode, allow_bypass:false, note:"", note_src:"", jump:null}]}]}' > "$TMP/solo.json"
}
rebuild() {  # [record] -> launch line of the manifest
  tmux -S "$SOCK" kill-session -t solo 2>/dev/null
  RR_REBUILD_DRYRUN=1 bash "$SCRIPTS/rr_rebuild.sh" "$TMP/solo.json" "$SOCK" "${1:-}" 2>/dev/null | jq -r .launch
}
mksnap true bypassPermissions
l=$(rebuild)
has   "no record: mode lowered to manual" "$l" "--permission-mode manual"
hasnt "no record: no bypass" "$l" "bypassPermissions"
hasnt "no record: no Remote Control" "$l" "--remote-control"
mksnap false ""
echo '{"permission_mode":"acceptEdits","permission_mode_explicit":true,"remote_control":false}' > "$TMP/rec_ex.json"
has   "mode unknown, explicit registration -> the registered mode" "$(rebuild "$TMP/rec_ex.json")" "--permission-mode acceptEdits"
echo '{"permission_mode":null,"permission_mode_explicit":false,"remote_control":false}' > "$TMP/rec_def.json"
hasnt "mode unknown, default registration -> no mode flag" "$(rebuild "$TMP/rec_def.json")" "--permission-mode"
echo '{"permission_mode":"bypassPermissions","remote_control":true}' > "$TMP/rec_old.json"
hasnt "mode unknown, old record with the old default -> no bypass" "$(rebuild "$TMP/rec_old.json")" "bypassPermissions"

echo "== 7. snapshot values cannot run shell code in the pane =="
cd "$TMP" || exit 1
EDIT_SNAP='(.windows[].panes[] | select(.claude)) |= (.rc_name = "x$(touch PWNED1)" | .model = "m$(touch PWNED2)\"; touch PWNED3; \"" | .config_dir_env = (.config_dir_env + "`touch PWNED4`"))' \
  one_hop "$TMP/rrF" eps "" ; out="$LAUNCH_OUT"; unset EDIT_SNAP
sleep 1
for f in PWNED1 PWNED2 PWNED3 PWNED4; do
  [[ ! -e "$TMP/$f" && ! -e "$TMP/rrF/$f" && ! -e "$HOME/$f" ]] && ok "no $f created" || bad "$f must not exist" "created"
done
has   "the session name arrived as literal text" "$out" 'NAME=x$(touch PWNED1)'
has   "the model arrived as literal text" "$out" 'ARG=m$(touch PWNED2)"; touch PWNED3; "'
EDIT_SNAP='(.windows[].panes[] | select(.claude)) |= (.session_id = "abc; touch PWNED5")' \
  ALIVE_WAIT=2 one_hop "$TMP/rrG" zeta ""; unset EDIT_SNAP
[[ ! -e "$TMP/PWNED5" ]] && ok "a non-UUID session id runs nothing" || bad "PWNED5" "created"
check "a non-UUID session id is not launched" "$LAUNCH_OUT" ""
check "the manifest marks it unresumable" "$(jq -r .status "$TMP/rrG/rr_manifest_424243.jsonl" 2>/dev/null)" "unresumable"

echo "== 8. no live agent after the resume: the lineage ends =="
has   "zeta: ended" "$(cat "$TMP/succ.zeta.out")" "ending the lineage"
hasnt "zeta: no connect-info as if resumed" "$(cat "$TMP/succ.zeta.out")" "sent connect-info"
[[ -z "$(ls "$TMP/rrG/registry/424243/"*.json 2>/dev/null)" ]] && ok "zeta: nothing carried to the next job" \
  || bad "nothing may be carried" "$(ls "$TMP/rrG/registry/424243/")"
has   "zeta: user told" "$(cat "$TMP/rrG/notifications.log")" "Resurrection stops here"
# Control: a hop whose agent is alive carries on (case 1).
has   "alpha (alive): connect-info sent" "$(cat "$TMP/succ.alpha.out")" "sent connect-info"

echo "== 9. a socket directory other users can reach is not used =="
LOOSE="$TMP/loose"; mkdir -p "$LOOSE"; chmod 755 "$LOOSE"
EDIT_SNAP=".tmux_socket = \"$LOOSE/default\"" one_hop "$TMP/rrH" eta "--permission-mode plan"; unset EDIT_SNAP
CAR="$TMP/rrH/registry/424243/eta.json"
nsock=$(jq -r .tmux_socket "$CAR" 2>/dev/null)
[[ -n "$nsock" && "$nsock" != "$LOOSE/default" && "$nsock" == "$TMP"/rr-tmux.*/default ]] \
  && ok "rebuilt on a fresh private socket ($nsock)" || bad "fallback socket" "got '$nsock'"
[[ ! -e "$LOOSE/default" ]] && ok "no server started in the loose directory" || bad "loose dir" "socket created"
check "the fallback directory is private" "$(stat -c %a "$(dirname "$nsock")" 2>/dev/null)" "700"
has   "the rebuilt session runs there" "$(tmux -S "$nsock" list-sessions -F '#S' 2>/dev/null)" "eta"
has   "the user is told" "$(cat "$TMP/rrH/notifications.log")" "did not use the tmux socket $LOOSE/default"
has   "the attach command names the new socket" "$(cat "$TMP/rrH/notifications.log")" "tmux -S $nsock attach"
tmux -S "$nsock" kill-server 2>/dev/null
check "rr_rebuild alone refuses the loose socket" \
  "$(RR_REBUILD_DRYRUN=1 bash "$SCRIPTS/rr_rebuild.sh" "$TMP/solo.json" "$LOOSE/x" 2>&1 >/dev/null | grep -c refusing)" "1"
check "rr_snapshot refuses it" "$(bash "$SCRIPTS/rr_snapshot.sh" "$LOOSE/x" solo /dev/null 2>&1 | grep -c refusing)" "1"

finish
