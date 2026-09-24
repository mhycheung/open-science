#!/bin/bash
# Tests for the coupling of slurm-resurrect to the open-science core's session
# jumps: the shared pane lock, finding a pane's jump record, the snapshot
# plumbing that carries it across a hop, rr_deliver.sh's recovery matrix, the
# jump inhibit, and the verified prompt delivery.
#
#   bash tests/slurm_resurrect/test_jump_recovery.sh
#
# Self-contained (see lib.sh): a private tmux server and state dirs, a fake config
# dir, stub scheduler commands, and a process genuinely named `claude` (a copy of
# sleep) so rr_resolve_claude's liveness gate is exercised for real.
# Sections 8-9 run the core's real jump.sh. They look for it in
# plugins/open-science-context/scripts of this repo, or in $RR_TEST_CORE_SCRIPTS, and are
# skipped when neither exists.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
rr_test_setup
CORE="${RR_TEST_CORE_SCRIPTS:-$T_HERE/../../plugins/open-science-context/scripts}"
[[ -f "$CORE/jump.sh" && -f "$CORE/cm_lib.sh" ]] || CORE=""

# squeue stub: job 111111 is RUNNING, everything else has left the queue.
job 111111 RUNNING

source "$SCRIPTS/rr_common.sh"

echo "== 1. pane lock: one file per (socket, pane), the core's own path =="
start_tmux t
PANE=$(tmux -S "$SOCK" display-message -p -t t:0.0 '#{pane_id}')
l_by_id=$(rr_pane_lock "$SOCK" "$PANE")        # how the core addresses it
l_by_tgt=$(rr_pane_lock "$SOCK" "t:0.0")       # how rr addresses it
check "same pane via %id and session:win.pane -> same lock" "$l_by_id" "$l_by_tgt"
l_other=$(rr_pane_lock "$TMP/some-other.sock" "$PANE")
[[ "$l_other" != "$l_by_id" ]] && ok "same pane_id on a different socket -> different lock" \
  || bad "socket must be part of the key" "both were $l_by_id"
KEY=$(rr_os_key "$SOCK" "$PANE")
check "lock lives at <core state>/lock/<key>.lock" "$l_by_id" "$OPSCI_STATE_DIR/lock/$KEY.lock"
if [[ -n "$CORE" ]]; then
  core_lock=$(OPSCI_STATE_DIR="$OPSCI_STATE_DIR" bash -c '. "$1/cm_lib.sh"; cm_lock_path "$(cm_pane_key "$2" "$3")"' _ "$CORE" "$SOCK" "$PANE")
  check "the core computes the same lock file" "$core_lock" "$l_by_id"
fi

echo "== 2. lock actually excludes =="
( exec 9>"$l_by_id"; flock -n 9 || exit 9
  ( exec 8>"$l_by_tgt"; flock -w 1 8 && echo LOCKED2 ) & wait $!
) >"$TMP/lockout" 2>&1
grep -q LOCKED2 "$TMP/lockout" && bad "second holder must block" "it acquired the lock" \
  || ok "a second injector cannot take a held pane lock"

echo "== 3. finding a pane's jump record =="
tmux -S "$SOCK" send-keys -t t:0.0 "CLAUDE_CONFIG_DIR=$TMP/cfg exec $TMP/bin/claude 600" Enter
sleep 3
CPID=$(pgrep -f "$TMP/bin/claude 600" | head -1)
[[ -n "$CPID" ]] || { echo "could not start the fake claude; aborting"; exit 1; }
SID=aaaabbbb-1111-2222-3333-444455556666
jq -n --arg s "$SID" '{sessionId:$s,status:"idle"}' > "$TMP/cfg/sessions/$CPID.json"
CTX="$TMP/proj/context.md"; mkdir -p "$TMP/proj/config"; echo ctx > "$CTX"; echo x > "$TMP/proj/AGENTS.md"; echo "x: 1" > "$TMP/proj/config/framework.yaml"
JD="$OPSCI_STATE_DIR/jump"; mkdir -p "$JD/done"
REC="$JD/$KEY.json"
# mkrec <file> <kind> <phase> [pid] [old_sid] [phase_at]
mkrec() {
  jq -n --arg k "$2" --arg ph "$3" --arg sf "$TMP/cfg/sessions/${4:-$CPID}.json" \
        --arg sid "${5:-$SID}" --arg ctx "$CTX" --arg sock "$SOCK" --arg pane "$PANE" \
        --arg key "$KEY" --arg at "${6:-$(date -Iseconds)}" \
    '{version:1, kind:$k, context:$ctx,
      prompt:(if $k=="active" then "/open-science-context:continue-context "+$ctx else "" end),
      sock:$sock, pane:$pane, key:$key, state_file:$sf, old_sid:$sid,
      requested_at:$at, phase:$ph, phase_at:$at}' > "$1"
}
find_rec() { rr_jump_find "$SOCK" "$PANE" "$CPID" "$SID" "$TMP/cfg"; }

mkrec "$REC" wait running
check "pending record of this claude process is found" "$(find_rec | jq -r .rr_where)" "pending"
mkrec "$REC" wait running 99999999
check "pending record naming another claude pid is ignored" "$(find_rec)" ""
rm -f "$REC"
check "no record -> nothing" "$(find_rec)" ""

T0=$(( $(date +%s) - 3600 ))
mkrec "$JD/done/$KEY-$T0.json" wait done "" "" "$(date -d @$T0 -Iseconds)"
check "finished wait jump, no activity since -> found" "$(find_rec | jq -r '.rr_where + "/" + .kind')" "done/wait"
touch "$TMP/cfg/projects/p/$SID.jsonl"     # the session did something after the jump
check "finished wait jump, session active since -> ignored" "$(find_rec)" ""
touch -d @$T0 "$TMP/cfg/projects/p/$SID.jsonl"
check "activity no later than the jump -> found again" "$(find_rec | jq -r .kind)" "wait"
mkrec "$JD/done/$KEY-$((T0+10)).json" active done "" "" "$(date -d @$((T0+10)) -Iseconds)"
check "newest finished record is an active jump -> nothing" "$(find_rec)" ""
rm -f "$JD/done/$KEY-$((T0+10)).json"
mkrec "$JD/done/$KEY-$((T0+20)).json" wait failed "" "" "$(date -d @$((T0+20)) -Iseconds)"
check "newest finished wait jump FAILED -> nothing" "$(find_rec)" ""
rm -f "$JD/done/$KEY-$((T0+20)).json"
jq '.rr_handled="7"' "$JD/done/$KEY-$T0.json" > "$TMP/x" && mv "$TMP/x" "$JD/done/$KEY-$T0.json"
check "record already handled by an earlier hop -> nothing" "$(find_rec)" ""
rm -f "$JD/done/"*

echo "== 4. snapshot carries the jump, and where the core is =="
export RR_SELFREG_DIR="$TMP/rr/panes"
export RR_PANE_NOTES_DIR="$TMP/rr/notes"
mkdir -p "$RR_SELFREG_DIR" "$RR_PANE_NOTES_DIR"
mkrec "$REC" active cleared
mkdir -p "$TMP/cfg/plugins/cache/mk/open-science-context/0.1/scripts"
touch "$TMP/cfg/plugins/cache/mk/open-science-context/0.1/scripts/"{jump.sh,cm_lib.sh}
bash "$SCRIPTS/rr_snapshot.sh" "$SOCK" t "$TMP/snap.json" 2>"$TMP/snap.err"
P0='.windows[0].panes[0]'
check "pane detected as claude"        "$(jq -r "$P0.claude" "$TMP/snap.json")" "true"
check "jump captured into snapshot"    "$(jq -r "$P0.jump.kind" "$TMP/snap.json")" "active"
check "jump phase preserved"           "$(jq -r "$P0.jump.phase" "$TMP/snap.json")" "cleared"
check "jump_src recorded"              "$(jq -r "$P0.jump_src" "$TMP/snap.json")" "$REC"
check "core found in the config dir's plugin cache" "$(jq -r "$P0.jump.rr_core_scripts" "$TMP/snap.json")" \
  "$TMP/cfg/plugins/cache/mk/open-science-context/0.1/scripts"
rm -rf "$TMP/cfg/plugins"
bash "$SCRIPTS/rr_snapshot.sh" "$SOCK" t "$TMP/snap1.json" 2>/dev/null
check "no core installed -> rr_core_scripts empty" "$(jq -r "$P0.jump.rr_core_scripts" "$TMP/snap1.json")" ""

echo "== 5. a record from an earlier claude process in this pane is not carried =="
mkrec "$REC" active running 99999999
bash "$SCRIPTS/rr_snapshot.sh" "$SOCK" t "$TMP/snap2.json" 2>/dev/null
check "stale record dropped" "$(jq -r "$P0.jump" "$TMP/snap2.json")" "null"
mkrec "$REC" active cleared

echo "== 6. note + jump on one pane: jump wins, note set aside =="
PK=$(printf '%s' "$PANE" | tr -c 'A-Za-z0-9._-' '_')
echo "my checkpoint note" > "$RR_PANE_NOTES_DIR/$PK.txt"
bash "$SCRIPTS/rr_snapshot.sh" "$SOCK" t "$TMP/snap3.json" 2>/dev/null
check "note suppressed in favour of the jump" "$(jq -r "$P0.note" "$TMP/snap3.json")" ""
check "jump still present"                    "$(jq -r "$P0.jump.kind" "$TMP/snap3.json")" "active"
[[ -f "$RR_PANE_NOTES_DIR/$PK.txt.superseded" ]] \
  && ok "note preserved as .superseded (never silently deleted)" \
  || bad "note must be preserved" "no .superseded file"
rm -f "$REC" "$RR_PANE_NOTES_DIR/$PK.txt.superseded"

echo "== 7. rr_deliver recovery matrix (dry run) =="
CP="/open-science-context:continue-context $CTX"
mk() {  # mk <where> <kind> <phase> [old_sid]   (snapshot session id is "sid")
  jq -nc --arg w "$1" --arg k "$2" --arg ph "$3" --arg old "${4:-sid}" --arg ctx "$CTX" \
    --arg core "$TMP/fakecore" \
    '{socket:"S",target:"T",status:"idle",session_id:"sid",note:"",note_src:"",jump_src:"",
      jump:{version:1,kind:$k,phase:$ph,context:$ctx,old_sid:$old,rr_where:$w,rr_core_scripts:$core,
            prompt:(if $k=="active" then "/open-science-context:continue-context "+$ctx else "" end)}}'
}
mkdir -p "$TMP/fakecore"; touch "$TMP/fakecore/"{jump.sh,cm_lib.sh}
run() { RR_DELIVER_DRYRUN=1 bash "$SCRIPTS/rr_deliver.sh" "$TMP/m.jsonl" 2>&1; }

mk pending active requested > "$TMP/m.jsonl"; out=$(run)
grep -qF "DRYRUN redrive -> T: $CP" <<<"$out" && ! grep -q "DRYRUN inject" <<<"$out" \
  && ok "pending active, not cleared -> re-drive through the core worker" || bad "pending active requested" "$out"
mk pending wait running > "$TMP/m.jsonl"; out=$(run)
grep -qF "DRYRUN redrive -> T: $CP" <<<"$out" \
  && ok "pending wait, not cleared -> re-drive as active with continue-context" || bad "pending wait running" "$out"
mk pending active cleared other > "$TMP/m.jsonl"; out=$(run)
grep -qF "DRYRUN inject -> T: $CP" <<<"$out" && ! grep -q "DRYRUN redrive" <<<"$out" \
  && ok "pending active, cleared -> deliver the prompt, no second clear" || bad "pending cleared" "$out"
mk pending wait unconfirmed other > "$TMP/m.jsonl"; out=$(run)
grep -qF "DRYRUN inject -> T: $CP" <<<"$out" && ! grep -q "DRYRUN redrive" <<<"$out" \
  && ok "unconfirmed, but the session id did change -> treated as cleared" || bad "unconfirmed changed" "$out"
mk pending wait unconfirmed sid > "$TMP/m.jsonl"; out=$(run)
grep -q "DRYRUN redrive" <<<"$out" \
  && ok "unconfirmed, session id unchanged -> re-drive" || bad "unconfirmed same" "$out"
mk done wait done > "$TMP/m.jsonl"; out=$(run)
grep -qF "DRYRUN inject -> T: $CP" <<<"$out" && grep -q "woke T" <<<"$out" \
  && ok "finished wait jump, waker gone -> wake with continue-context" || bad "done wait" "$out"
mk pending active failed > "$TMP/m.jsonl"; out=$(run)
grep -q "DRYRUN inject\|DRYRUN redrive" <<<"$out" && bad "failed record must be left alone" "$out" \
  || ok "pending record in an unknown phase -> left alone"

# Without the core the plugin still works: a re-drive falls back to delivering
# the prompt without clearing, and says so.
mk pending active requested | jq -c '.jump.rr_core_scripts=""' > "$TMP/m.jsonl"; out=$(run)
grep -q "open-science core not found" <<<"$out" && grep -qF "DRYRUN inject -> T: $CP" <<<"$out" \
  && ! grep -q "DRYRUN redrive" <<<"$out" \
  && ok "no core: re-drive falls back to delivering the prompt" || bad "no-core fallback" "$out"

echo '{"socket":"S","target":"T","status":"idle","session_id":"sid","note":"hi","note_src":"","jump":null}' > "$TMP/m.jsonl"
out=$(run)
grep -q "DRYRUN inject -> T: hi" <<<"$out" && ok "note-only pane still delivered" || bad "note-only" "$out"

echo '{"socket":"S","target":"T","status":"idle","session_id":"sid","note":"","note_src":"","jump":null}' > "$TMP/m.jsonl"
out=$(run)
grep -q "DRYRUN inject" <<<"$out" && bad "silent pane" "it was poked: $out" \
  || ok "pane with neither note nor jump is left silent"

# A stand-in Claude TUI for sections 8-9: a process named `claude` (a copy of
# bash) that writes its own sessions/<pid>.json, renders a '❯' input line and a
# box border, records every submitted line, and on `/clear` changes its session
# id, which is how the core's worker confirms a clear.
mkdir -p "$TMP/bin/tc"; cp "$(command -v bash)" "$TMP/bin/tc/claude"
cat > "$TMP/bin/faketui_clear" <<'FTC'
buf=""; STATUS=""; n=0
SF="$FT_CFG/sessions/$$.json"
state() { printf '{"sessionId":"%s","status":"%s"}\n' "$1" "$2" > "$SF.tmp" && mv "$SF.tmp" "$SF"; }
sid="$FT_SID"; state "$sid" idle
draw(){ printf '\033[2J\033[H%s\n❯ %s\n%s\n' "$STATUS" "$buf" "────────────────────"; }
draw
while IFS= read -r -N1 c; do
  case "$c" in
    $'\n'|$'\r')
      if [[ "$buf" == /clear ]]; then
        printf '%s\n' "$buf" >> "$FT_OUT"; buf=""; n=$((n+1)); sid="cleared-$n-$$"; state "$sid" idle
      elif [[ -n "$buf" ]]; then
        printf '%s\n' "$buf" >> "$FT_OUT"; buf=""; STATUS="esc to interrupt"; state "$sid" busy
        draw; sleep 3; STATUS=""; state "$sid" idle
      fi ;;
    $'\025') buf="" ;;
    $'\177') buf="${buf%?}" ;;
    *)       buf="$buf$c" ;;
  esac
  draw
done
FTC
start_tui() {  # <window index> <out file> <session id> -> prints pane id
  tmux -S "$SOCK" new-window -d -t "t:$1" -n "tui$1"
  tmux -S "$SOCK" send-keys -t "t:$1" "FT_CFG=$TMP/cfg FT_OUT=$2 FT_SID=$3 exec $TMP/bin/tc/claude $TMP/bin/faketui_clear" Enter
  local p i; p=$(tmux -S "$SOCK" display-message -p -t "t:$1" '#{pane_pid}')
  for ((i=0; i<50; i++)); do [[ -f "$TMP/cfg/sessions/$p.json" ]] && break; sleep 0.2; done
  tmux -S "$SOCK" display-message -p -t "t:$1" '#{pane_id}'
}
# A manifest line for the rebuilt pane t:<win>, carrying a pending wait jump that
# the old job's pane (another socket) had in phase `running`.
mk_real() {  # <win> <session id> <core dir or empty> <source record path>
  local oldkey; oldkey=$(rr_os_key "$TMP/old.sock" "%9")
  jq -n --arg k wait --arg ctx "$CTX" --arg sf "$TMP/cfg/sessions/1.json" --arg sid "$2" \
        --arg key "$oldkey" --arg sock "$TMP/old.sock" \
    '{version:1, kind:$k, context:$ctx, prompt:"", sock:$sock, pane:"%9", key:$key,
      state_file:$sf, old_sid:$sid, requested_at:(now|todate), phase:"running"}' > "$4"
  jq -nc --arg sock "$SOCK" --arg t "t:$1" --arg sid "$2" --arg core "$3" --arg src "$4" \
     --slurpfile r "$4" \
    '{socket:$sock, target:$t, status:"idle", session_id:$sid, note:"", note_src:"",
      jump_src:$src, jump:($r[0] + {rr_where:"pending", rr_src:$src, rr_core_scripts:$core})}'
}
real_deliver() { RR_BOOT_WAIT=0 RR_SETTLE_WAIT=0 OPSCI_IDLE_STABLE=1 bash "$SCRIPTS/rr_deliver.sh" "$1" 2>&1; }

if [[ -n "$CORE" ]]; then
echo "== 8. re-drive through the core's real jump.sh worker =="
P8=$(start_tui 30 "$TMP/tc30.out" s30)
SRC8="$JD/$(rr_os_key "$TMP/old.sock" "%9").json"
mk_real 30 s30 "$CORE" "$SRC8" > "$TMP/m8.jsonl"
out=$(real_deliver "$TMP/m8.jsonl")
grep -q "started the core's jump worker for t:30" <<<"$out" && ok "worker started for the rebuilt pane" \
  || bad "worker start" "$out"
K8=$(rr_os_key "$SOCK" "$P8")
for ((i=0; i<60; i++)); do ls "$JD/done/$K8"-*.json >/dev/null 2>&1 && break; sleep 1; done
D8=$(ls -t "$JD/done/$K8"-*.json 2>/dev/null | head -1)
check "worker finished the re-driven jump" "$(jq -r .phase "$D8" 2>/dev/null)" "done"
check "re-driven as active"               "$(jq -r '.kind + "/" + .rr_orig_kind' "$D8" 2>/dev/null)" "active/wait"
check "request rewritten for the rebuilt pane" "$(jq -r .pane "$D8" 2>/dev/null)" "$P8"
check "pane saw /clear, then the resume prompt" "$(cat "$TMP/tc30.out" 2>/dev/null)" "/clear"$'\n'"$CP"
[[ ! -f "$SRC8" ]] && ls "$JD/done/$(basename "$SRC8" .json)"-*.json >/dev/null 2>&1 \
  && ok "source record archived to done/" || bad "source record archive" "$(ls "$JD" "$JD/done")"
check "archived source record is marked handled" \
  "$(jq -r .rr_handled "$(ls -t "$JD/done/$(basename "$SRC8" .json)"-*.json | head -1)" 2>/dev/null)" "$SLURM_JOB_ID"

# Control: no core anywhere -> the prompt is delivered, but nothing is cleared.
P8b=$(start_tui 31 "$TMP/tc31.out" s31)
mk_real 31 s31 "" "$JD/src31.json" > "$TMP/m8b.jsonl"
out=$(real_deliver "$TMP/m8b.jsonl")
grep -q "open-science core not found" <<<"$out" && ok "no core: says so" || bad "no-core message" "$out"
check "no core: prompt delivered, no /clear typed" "$(cat "$TMP/tc31.out" 2>/dev/null)" "$CP"

echo "== 9. jump inhibit: the core's jump.sh honours the files this plugin writes =="
mkdir -p "$RR_REG_ROOT/424242"
jq -n --arg sock "$SOCK" '{tmux_socket:$sock, tmux_session:"t"}' > "$RR_REG_ROOT/424242/t.json"
rr_inhibit_panes 424242 "slurm-resurrect test: wind-down"
[[ -f "$OPSCI_STATE_DIR/inhibit_jump_$KEY" && -f "$OPSCI_STATE_DIR/inhibit_jump_$K8" ]] \
  && ok "inhibit file written for each Claude pane" || bad "inhibit files" "$(ls "$OPSCI_STATE_DIR")"
PPROBE=$(tmux -S "$SOCK" new-window -d -P -F '#{pane_id}' -t t:40)
[[ ! -e "$OPSCI_STATE_DIR/inhibit_jump_$(rr_os_key "$SOCK" "$PPROBE")" ]] \
  && ok "no inhibit file for a plain shell pane" || bad "plain pane inhibited" ""
# A context file older than the core's freshness limit: jump.sh checks the inhibit
# first, and without one it refuses on the age instead, so it never gets as far
# as looking for a Claude process (the test itself may run under one).
STALE="$TMP/proj/stale.md"; echo old > "$STALE"; touch -d "2 hours ago" "$STALE"
jr() { TMUX="$SOCK,1,0" TMUX_PANE="$1" bash "$CORE/jump.sh" wait "$STALE" 2>&1; echo "rc=$?"; }
out=$(jr "$PANE")
grep -q "inhibited" <<<"$out" && grep -q "rc=1" <<<"$out" && grep -q "wind-down" <<<"$out" \
  && ok "jump.sh refuses a jump in an inhibited pane, with our message" || bad "inhibit refusal" "$out"
rr_uninhibit 424242
[[ ! -e "$OPSCI_STATE_DIR/inhibit_jump_$KEY" && ! -e "$RR_HOME/inhibit_424242.list" ]] \
  && ok "uninhibit removes the files and the list" || bad "uninhibit" "$(ls "$OPSCI_STATE_DIR")"
out=$(jr "$PANE")
grep -q "last modified" <<<"$out" && ! grep -q "inhibited" <<<"$out" \
  && ok "control: after removal jump.sh gets past the inhibit check" \
  || bad "control: no inhibit after removal" "$out"
# The sweep drops the files of jobs no longer queued and keeps a running job's.
touch "$OPSCI_STATE_DIR/inhibit_jump_x1" "$OPSCI_STATE_DIR/inhibit_jump_x2"
echo "$OPSCI_STATE_DIR/inhibit_jump_x1" > "$RR_HOME/inhibit_555.list"
echo "$OPSCI_STATE_DIR/inhibit_jump_x2" > "$RR_HOME/inhibit_111111.list"
rr_inhibit_sweep 0
[[ ! -e "$OPSCI_STATE_DIR/inhibit_jump_x1" && -e "$OPSCI_STATE_DIR/inhibit_jump_x2" ]] \
  && ok "sweep: ended job's inhibit removed, running job's kept" || bad "sweep" "$(ls "$OPSCI_STATE_DIR")"
pkill -f "jump.sh --worker $JD" 2>/dev/null
else
  echo "== 8-9. SKIPPED: the open-science core is not in this tree (set RR_TEST_CORE_SCRIPTS to run them) =="
fi

echo "== 10. invisible-character handling in the delivery guard =="
# Regression for a measured false negative (2026-08-09): the TUI pads its empty
# prompt line with U+00A0 NO-BREAK SPACE, and byte-oriented `tr -d '[:space:]'`
# leaves it. rr_pane_input_empty therefore called an EMPTY buffer non-empty, every
# successful delivery looked like a failure, and the retry loop submitted the same
# prompt three times into a live session.
check "U+00A0 stripped"        "$(printf 'a\xc2\xa0b' | rr_strip_blank)" "ab"
check "U+200B stripped"        "$(printf 'a\xe2\x80\x8bb' | rr_strip_blank)" "ab"
check "BOM stripped"           "$(printf 'a\xef\xbb\xbfb' | rr_strip_blank)" "ab"
check "ASCII space stripped"   "$(printf 'a b\tc\nd' | rr_strip_blank)" "abcd"
check "visible text preserved" "$(printf '/context-management Resume' | rr_strip_blank)" "/context-managementResume"

# A prompt line padded exactly the way the real TUI pads it must read as empty.
tmux -S "$SOCK" new-window -d -t t -n probe
sleep 1
tmux -S "$SOCK" send-keys -t t:probe "clear; printf '\\xe2\\x9d\\xaf\\xc2\\xa0\\n'" Enter
sleep 2
rr_pane_input_empty "$SOCK" t:probe \
  && ok "prompt line padded with U+00A0 reads as EMPTY" \
  || bad "NBSP-padded prompt must read as empty" "it read as non-empty (the 2026-08-09 bug)"
tmux -S "$SOCK" send-keys -t t:probe "clear; printf '\\xe2\\x9d\\xaf leftover text\\n'" Enter
sleep 2
rr_pane_input_empty "$SOCK" t:probe \
  && bad "a prompt line with real text must NOT read as empty" "it read as empty" \
  || ok "prompt line with real content reads as NON-empty"

echo "== 11. the resume always wins the input box =="
# Regression for the 2026-08-09 stall on pane %2. An un-submitted line sat in the
# pane's input box; rr_pane_deliver refused to touch a buffer it had not written
# and returned without delivering, three jumps running (08-08 23:56, 00:17,
# 08-09 13:06). The last of those left the pane cleared, empty and idle for hours
# holding a finished job, because only a resurrection replays a pending record
# and the next hop was two days out. Policy now: discard whatever is in the box,
# deliver the resume, and NEVER leave un-submitted text behind.
#
# Driven against a real tmux pane running a minimal stand-in for the Claude input
# widget: it renders the live buffer on a '❯ ' line, honours C-u and BSpace, and
# on Enter records the submitted line and goes "busy" the way the real TUI does.
cat > "$TMP/bin/faketui" <<'FAKETUI'
#!/bin/bash
buf=""; STATUS=""
draw(){ printf '\033[2J\033[H%s\n❯ %s\n' "$STATUS" "$buf"; }
draw
while IFS= read -r -N1 c; do
  case "$c" in
    $'\n'|$'\r')
      if [[ -n "$buf" && "${FAKETUI_NODRAIN:-0}" != "1" ]]; then
        printf '%s\n' "$buf" >> "$FAKETUI_OUT"
        buf=""; STATUS="esc to interrupt"; draw; sleep 3; STATUS=""
      fi ;;
    $'\025') buf="" ;;          # C-u
    $'\177') buf="${buf%?}" ;;  # BSpace
    *)       buf="$buf$c" ;;
  esac
  draw
done
FAKETUI
chmod +x "$TMP/bin/faketui"

PAYLOAD="/context-management Resume the campaign: read main_context.md and continue from In flight."
STRAY="send me the waveform plots when they're done"

# --- 11a. foreign text in the box must be overridden, not deferred to ----------
tmux -S "$SOCK" new-window -d -t t:20 -n tui1
tmux -S "$SOCK" send-keys -t t:tui1 \
  "FAKETUI_OUT=$TMP/tui1.out exec $TMP/bin/faketui" Enter
sleep 2
tmux -S "$SOCK" send-keys -t t:tui1 "$STRAY"    # a human composing; never submitted
sleep 1
rr_pane_input_empty "$SOCK" t:tui1 \
  && bad "test setup: stray text should be in the box" "box read as empty" \
  || ok "setup: stray un-submitted text is in the input box"

rr_pane_deliver "$SOCK" t:tui1 "$PAYLOAD" 2 >"$TMP/d1.log" 2>&1; rc1=$?
check "delivery succeeds despite foreign text" "$rc1" "0"
grep -q "ABORTING" "$TMP/d1.log" \
  && bad "must not abort on foreign text" "$(grep ABORTING "$TMP/d1.log")" \
  || ok "no ABORTING -- the resume was not deferred to the stray text"
grep -q "DISCARDING" "$TMP/d1.log" \
  && ok "the discarded text is logged, not silently vaporised" \
  || bad "discarded text must be logged" "$(tail -3 "$TMP/d1.log")"
grep -qF "$PAYLOAD" "$TMP/tui1.out" 2>/dev/null \
  && ok "the resume prompt was actually submitted" \
  || bad "resume must reach the TUI" "submitted: $(cat "$TMP/tui1.out" 2>/dev/null)"
grep -qF "$STRAY" "$TMP/tui1.out" 2>/dev/null \
  && bad "the stray line must be discarded, not submitted" "it was submitted" \
  || ok "the stray line was discarded rather than sent"

# --- 11b. a FAILED delivery must never leave text hanging ---------------------
# FAKETUI_NODRAIN makes Enter a no-op, so every attempt fails the drain check.
tmux -S "$SOCK" new-window -d -t t:21 -n tui2
tmux -S "$SOCK" send-keys -t t:tui2 \
  "FAKETUI_OUT=$TMP/tui2.out FAKETUI_NODRAIN=1 exec $TMP/bin/faketui" Enter
sleep 2
tmux -S "$SOCK" send-keys -t t:tui2 "$STRAY"
sleep 1
rr_pane_deliver "$SOCK" t:tui2 "$PAYLOAD" 1 >"$TMP/d2.log" 2>&1; rc2=$?
check "an undeliverable prompt reports failure" "$rc2" "1"
rr_pane_input_empty "$SOCK" t:tui2 \
  && ok "failed delivery leaves the input box EMPTY (nothing hanging)" \
  || bad "no jump may leave un-submitted text in the box" \
         "box still holds: $(tmux -S "$SOCK" capture-pane -p -t t:tui2 | grep '❯' | tail -1)"

echo "== 12. a prompt whose head has SCROLLED off the input box still delivers =="
# Regression for the 2026-08-09 stranding. The real TUI wraps a long prompt to the
# pane width and, once it is taller than the input box, scrolls it to keep the
# cursor visible -- so the HEAD is not rendered and capture-pane cannot see it.
# rr_pane_deliver used to require the head, call its absence "buffer LOST THE HEAD",
# retry identically four times and then CLEAR THE BOX and report failure, throwing
# away a delivery whose buffer was complete. That is what stranded the T2 resumes of
# 2026-08-09 17:17 and 18:13 (4/4 attempts each) on a 52-column pane. Verified
# against the real Claude Code TUI the same day: widening the pane without touching
# the buffer made the head reappear, and a 585-char prompt now submits on attempt 1.
#
# This stand-in reproduces the viewport: it wraps the buffer at FAKETUI_WRAP columns
# and renders only the LAST FAKETUI_BOXH wrapped lines, with the glyph on the first
# VISIBLE line, then a box border.
cat > "$TMP/bin/faketui_scroll" <<'FAKETUIS'
#!/bin/bash
buf=""; STATUS=""
WRAP="${FAKETUI_WRAP:-52}"; BOXH="${FAKETUI_BOXH:-8}"
draw(){
  local out="" n=0 i lines=()
  if [[ -n "$buf" ]]; then
    for ((i=0; i<${#buf}; i+=WRAP)); do lines+=("${buf:i:WRAP}"); done
  fi
  n=${#lines[@]}
  printf '\033[2J\033[H%s\n' "$STATUS"
  if (( n == 0 )); then
    printf '❯ \n'
  else
    local start=0
    (( n > BOXH )) && start=$(( n - BOXH ))
    for ((i=start; i<n; i++)); do
      if (( i == start )); then printf '❯ %s\n' "${lines[i]}"
      else printf '  %s\n' "${lines[i]}"; fi
    done
  fi
  printf '%s\n' "────────────────────────────"
}
draw
while IFS= read -r -N1 c; do
  case "$c" in
    $'\n'|$'\r')
      if [[ -n "$buf" && "${FAKETUI_NODRAIN:-0}" != "1" ]]; then
        printf '%s\n' "$buf" >> "$FAKETUI_OUT"
        buf=""; STATUS="esc to interrupt"; draw; sleep 3; STATUS=""
      fi ;;
    $'\025') buf="" ;;
    $'\177') buf="${buf%?}" ;;
    *)       buf="$buf$c" ;;
  esac
  draw
done
FAKETUIS
chmod +x "$TMP/bin/faketui_scroll"

# 585 chars at 52 columns is ~12 wrapped lines in an 8-line box: the head cannot be
# rendered. This is the exact geometry measured on pane %2.
LONG="/context-management Resume the HX tail-solver campaign: read main_context.md and continue from In flight and Next step. Round 8 has landed - compute the octave spectra of its three cells out to the full Nyquist and settle whether the defect-2 noise grows under refinement once de-aliased. This trailing sentence is padding whose only purpose is to push the wrapped form of the prompt past the height of the visible input box so that the beginning of it scrolls off the screen entirely."
tmux -S "$SOCK" new-window -d -t t:22 -n tui3
tmux -S "$SOCK" send-keys -t t:tui3 \
  "FAKETUI_OUT=$TMP/tui3.out FAKETUI_WRAP=52 FAKETUI_BOXH=8 exec $TMP/bin/faketui_scroll" Enter
sleep 2

rr_pane_deliver "$SOCK" t:tui3 "$LONG" 4 >"$TMP/d3.log" 2>&1; rc3=$?
check "a scrolled-head prompt delivers" "$rc3" "0"
grep -q "LOST THE HEAD" "$TMP/d3.log" \
  && bad "a scrolled head must not be called a lost head" "$(grep 'LOST THE HEAD' "$TMP/d3.log")" \
  || ok "no bogus LOST THE HEAD verdict"
grep -q "scrolled out" "$TMP/d3.log" \
  && ok "overflow is recognised as scrolling, and accepted" \
  || bad "expected the scrolled-head acceptance path" "$(tail -3 "$TMP/d3.log")"
grep -qF "$LONG" "$TMP/tui3.out" 2>/dev/null \
  && ok "the COMPLETE prompt reached the TUI, head included" \
  || bad "the whole prompt must be submitted" "got: $(cat "$TMP/tui3.out" 2>/dev/null)"
# Only one submission: an over-eager retry would send it twice.
check "submitted exactly once" "$(grep -cF "$LONG" "$TMP/tui3.out" 2>/dev/null)" "1"

echo "== 13. a paste collapsed to a placeholder must still FAIL =="
# The head check is relaxed above, so the tail check is what now rules out a paste
# the TUI swallowed into a "[Pasted text +N lines]" placeholder. If that stopped
# failing, a lost prompt would be reported as delivered -- worse than a false alarm.
cat > "$TMP/bin/faketui_placeholder" <<'FAKETUIP'
#!/bin/bash
buf=""; STATUS=""
draw(){
  printf '\033[2J\033[H%s\n' "$STATUS"
  if [[ -n "$buf" ]]; then printf '❯ [Pasted text +12 lines]\n'; else printf '❯ \n'; fi
  printf '%s\n' "────────────────────────────"
}
draw
while IFS= read -r -N1 c; do
  case "$c" in
    $'\n'|$'\r') if [[ -n "$buf" ]]; then printf '%s\n' "$buf" >> "$FAKETUI_OUT"; buf=""; fi ;;
    $'\025') buf="" ;;
    $'\177') buf="${buf%?}" ;;
    *)       buf="$buf$c" ;;
  esac
  draw
done
FAKETUIP
chmod +x "$TMP/bin/faketui_placeholder"
tmux -S "$SOCK" new-window -d -t t:23 -n tui4
tmux -S "$SOCK" send-keys -t t:tui4 \
  "FAKETUI_OUT=$TMP/tui4.out exec $TMP/bin/faketui_placeholder" Enter
sleep 2
rr_pane_deliver "$SOCK" t:tui4 "$LONG" 2 >"$TMP/d4.log" 2>&1; rc4=$?
check "a placeholder-collapsed paste reports failure" "$rc4" "1"
grep -q "missing the END" "$TMP/d4.log" \
  && ok "the tail check is what catches it" \
  || bad "expected a missing-END verdict" "$(tail -3 "$TMP/d4.log")"

finish
