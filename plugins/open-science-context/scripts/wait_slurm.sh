#!/usr/bin/env bash
# SLURM waker for a wait jump. Run it as a BACKGROUND Bash task (run_in_background):
#
#   wait_slurm.sh <jobid> [<jobid>...]
#
# It polls `squeue` every $OPSCI_WAIT_POLL seconds (default 60) and exits when none
# of the jobs is queued or running, printing each job's final state from `sacct`.
# Claude Code lists the running task in the Stop hook's `background_tasks`, so the
# cache-cold timer and the wait-jump check see it, and its exit wakes the session.
# Exit 0 when every job ended COMPLETED, 1 when any did not, 2 on a usage error.
set -uo pipefail
POLL="${OPSCI_WAIT_POLL:-60}"
[ $# -ge 1 ] || { echo "usage: wait_slurm.sh <jobid> [<jobid>...]" >&2; exit 2; }
for j in "$@"; do [[ "$j" =~ ^[0-9]+(_[0-9]+)?$ ]] || { echo "not a job id: $j" >&2; exit 2; }; done
command -v squeue >/dev/null 2>&1 || { echo "squeue not found: this is not a SLURM host" >&2; exit 2; }
ids=$(IFS=,; echo "$*")
while :; do
  left=$(squeue -h -j "$ids" -o %i 2>/dev/null | wc -l)
  [ "$left" -eq 0 ] && break
  sleep "$POLL"
done
rc=0
echo "open-science wait_slurm: jobs $ids have left the queue. Use open-science-context:continue-context if you have no context."
for j in "$@"; do
  st=$(sacct -n -X -j "$j" -o State%20 2>/dev/null | head -1 | tr -d ' ')
  echo "job $j: ${st:-unknown}"
  [ "${st:-}" = COMPLETED ] || rc=1
done
exit "$rc"
