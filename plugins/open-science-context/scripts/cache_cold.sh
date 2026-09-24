#!/usr/bin/env bash
# Cache-cold timer, started detached by cm_stop.sh when a session stops while
# something that will wake it is still running. The Stop hook kills and restarts
# it at every stop, so it fires only after $OPSCI_CACHE_COLD_MIN minutes (default
# 45; the prompt cache is assumed to last 1 hour) with no stop at all.
#
#   cache_cold.sh <sock> <pane> <pane key> <session id> <state file or "">
#
# When it fires it types ONE line into the pane, and only if: the pane is idle,
# the session id is unchanged (a cleared session has no warm cache to lose), and no
# jump is pending. Otherwise it exits quietly; the next stop re-arms it.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cm_lib.sh
. "$HERE/cm_lib.sh"
SOCK="$1"; PANE="$2"; KEY="$3"; SID="$4"; SF="${5:-}"
MIN="${OPSCI_CACHE_COLD_MIN:-45}"
SECS="${OPSCI_CACHE_COLD_SECONDS:-$(( MIN * 60 ))}"   # seconds override, for tests
if [ "$SECS" -ge 60 ]; then IDLE="$(( SECS / 60 )) min"; else IDLE="${SECS} s"; fi
NOTICE="[open-science] cache-cold: ${IDLE} idle with work still running. Do a wait jump now (open-science-context:context-management)."

sleep "$SECS"
me=$$
cleanup() { [ "$(cat "$(cm_timer_path "$KEY")" 2>/dev/null)" = "$me" ] && rm -f "$(cm_timer_path "$KEY")"; }
trap cleanup EXIT

[ -f "$(cm_request_path "$KEY")" ] && { cm_log "cache-cold $PANE: jump pending; not firing"; exit 0; }
if [ -n "$SF" ] && [ -n "$SID" ] && [ "$(cm_sid "$SF")" != "$SID" ]; then
  cm_log "cache-cold $PANE: session changed since arming; not firing"; exit 0
fi
exec 9>"$(cm_lock_path "$KEY")"
flock -w 60 9 || { cm_log "cache-cold $PANE: pane lock busy; not firing"; exit 0; }
if ! cm_wait_idle "$SOCK" "$PANE" "$SF" 60; then
  cm_log "cache-cold $PANE: pane not idle; not firing (the next stop re-arms)"; exit 0
fi
cm_deliver "$SOCK" "$PANE" "$NOTICE" && cm_log "cache-cold $PANE: notice delivered"
