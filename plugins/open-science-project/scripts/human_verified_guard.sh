#!/usr/bin/env bash
# PreToolUse hook (Edit|Write|MultiEdit|NotebookEdit) of the open-science-project plugin.
# Only the user sets `verification: human-verified` (AGENTS.md rule 5). This hook refuses
# an edit, inside a template project (symlinks resolved), after which the file holds more
# such settings than before, in any YAML spelling: exit 2, message on stderr, which Claude
# Code shows to the agent. The work is done by human_verified.py. Bash commands are not
# checked (a grep for the string must not be refused); the publish check is the backstop:
# it refuses a human-verified node whose `verification:` line was last changed in an agent
# commit. This guards against mistakes; it is not a security boundary.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
IN=$(cat)
if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$IN" | python3 "$HERE/human_verified.py"
    exit $?
fi
# No python3: refuse any payload that names human-verified after a verification key, in
# any quoting (stricter: an old_string also counts).
if printf '%s' "$IN" | tr -d '\\"'"'" | grep -Eq 'verification[^a-z]{0,40}human-verified'; then
    echo "open-science-project: only the user sets 'verification: human-verified' (AGENTS.md rule 5). An agent may set 'verified' with an 'evidence:' pointer. Leave human-verification to the user, and tell them the result is ready for it." >&2
    exit 2
fi
exit 0
