#!/bin/bash
# UserPromptSubmit hook of the slurm-resurrect plugin.
#
# Claude Code runs this hook only when the USER submits a prompt; the model can
# neither fire it nor invoke the /slurm-resurrect:resurrect skill (the skill sets
# disable-model-invocation). So when the submitted prompt is that slash command,
# the command comes from the user, and this hook runs it with RR_CALLER set --
# the only way a user-only command (register, reset, set, set-notify) is
# accepted from inside a Claude session. Any other prompt: exit 0, no output,
# nothing done.
#
# Output:
#   * first use of `register`: the prompt is BLOCKED and the first-use warning is
#     shown to the user as the block reason (the model never sees it, so it
#     cannot skip it). Nothing is registered.
#   * otherwise: the command's output is added to the model's context, and the
#     skill tells the model to relay it without re-running anything.
# Never fails the prompt on its own errors: always exits 0.
set -u
IN=$(cat 2>/dev/null || true)
command -v jq >/dev/null 2>&1 || exit 0
PROMPT=$(printf '%s' "$IN" | jq -r '.prompt // empty' 2>/dev/null)
# Only the literal slash command, at the very start of the prompt.
re='^/slurm-resurrect:resurrect([[:space:]]|$)'
[[ "$PROMPT" =~ $re ]] || exit 0

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARGS="${PROMPT#/slurm-resurrect:resurrect}"
ARGS="${ARGS#"${ARGS%%[![:space:]]*}"}"            # trim leading space
ARGS=$(printf '%s' "$ARGS" | tr '\n' ' ')
# Split on whitespace. Never eval: the prompt is untrusted text. set-notify
# takes the rest of the line as one argument so the command can hold spaces.
read -ra WORDS <<< "$ARGS"
if [[ "${WORDS[0]:-}" == "set-notify" ]]; then
  REST="${ARGS#set-notify}"; REST="${REST#"${REST%%[![:space:]]*}"}"
  WORDS=(set-notify "$REST")
fi
[[ ${#WORDS[@]} -gt 0 ]] || WORDS=(help)

OUT=$(RR_CALLER=user-prompt-hook bash "$HERE/rr_registry.sh" "${WORDS[@]}" 2>&1 < /dev/null)
RC=$?

if [[ $RC -eq 4 ]]; then
  jq -n --arg r "$OUT" '{decision:"block", reason:$r}'
  exit 0
fi
CTX="[slurm-resurrect] The plugin's UserPromptSubmit hook has already run \`rr_registry.sh ${WORDS[*]}\` for the user (exit status $RC). Its output:
$OUT"
jq -n --arg c "$CTX" --arg m "$OUT" \
  '{systemMessage:$m, hookSpecificOutput:{hookEventName:"UserPromptSubmit", additionalContext:$c}}'
exit 0
