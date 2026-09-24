# S0 verification: results of testing the design's unknowns

Tested 2026-09-23 on Claude Code 2.1.280. Each result gives the test, what was observed,
and what it changes in the design. Test sessions ran with user settings excluded
(`--setting-sources project,local`) unless stated, in a scratch project outside any other
project tree. Models: `claude-sonnet-5` for mechanism tests, `claude-opus-5-5` for the
skill-choice test.

## 1. `@AGENTS.md` import in `CLAUDE.md`

**Test.** `CLAUDE.md` contained the line `@AGENTS.md` and a canary of its own; `AGENTS.md`
contained a second canary. `claude -p` was asked, with no tools, to report every canary in
its instructions.

**Result: works.** Both canaries were reported exactly.

**Design consequence.** None. `CLAUDE.md` = `@AGENTS.md` + Claude-only material, as planned.

## 2. Plugins loaded with `--plugin-dir`

**Test.** A plugin named `open-science` with skills `context-management` and `hello` and a
`hooks/hooks.json` that dumps each hook's stdin to a file, with commands written as
`bash ${CLAUDE_PLUGIN_ROOT}/hooks/dump.sh <event>`.

**Results.**
- Skills are namespaced: the session's init message lists `open-science:context-management`
  and `open-science:hello`, and the model sees those names.
- Plugin hooks work. `SessionStart`, `UserPromptSubmit`, `Stop`, `SubagentStart` and
  `SubagentStop` all fired, and `${CLAUDE_PLUGIN_ROOT}` resolved.
- A plugin skill and a personal skill with the same base name coexist. Both
  `context-management` and `open-science:context-management` were listed.
- **Which one the model picks.** With personal skills loaded and the plugin skill given the
  same description as the personal one, the prompt "load your context-management skill"
  made the model invoke the unprefixed personal skill in 2 of 2 runs. It noticed the plugin
  version and did not load it.

**Design consequences.**
- Skills inside the plugin refer to each other by their full name
  (`open-science:continue-context`), and resume prompts written by jumps use the full name.
- A user who installs the plugin next to older skills with the same names gets the old
  ones by default. The install instructions (README, and the `live-config` handover) must
  say to retire or disable same-named personal skills, or to always use the prefix.
- Restricting setting sources to `project,local` also drops the user's personal skills.
  Tests that need personal skills must load user settings, which also loads the user's
  hooks; such tests must run with any tmux/SLURM environment that those hooks act on
  removed.

## 3. Completion notices after `/clear` (the wait jump)

**Test.** Interactive session in a private tmux server. In one turn the session started a
background subagent (Agent tool, `run_in_background`), a background Bash command
(`sleep 85; echo …`) and a Monitor (`sleep 95; echo …`), then ended its turn. `/clear` was
sent while all three were still running (the `Stop` payload just before the clear listed
all three as `running`). The session was then left alone for three minutes.

**Result: all three reach the new conversation.** `SessionStart` fired with
`source: "clear"` and a new session id. The new conversation then received, as user turns:
- the subagent: `<task-notification>` with `<status>completed</status>` and the full
  `<result>` text;
- the Bash command: `<task-notification>` with a summary, the exit code and the output
  file path (the output itself is not inlined);
- the Monitor: `<task-notification>` with `<event>` carrying the emitted line.

Each notice started a turn in the cleared session.

**Caveat.** The test subagent put its own `sleep` in the background, returned once
(before the clear), and was resumed when that background command finished. The notice
delivered after the clear came from the resumed run. The notice text says a subagent
notifies "each time this agent stops with no live background children of its own", so one
subagent can produce several notices.

**Design consequence.** None to the mechanism: the wait jump can rely on all three kinds of
waker. The jump's resume logic must accept repeated notices from one subagent.

## 4. `SubagentStart` and `SubagentStop`

**Result: both fire** for foreground and background subagents.
- `SubagentStart` input: `session_id`, `transcript_path`, `cwd`, `prompt_id`, `agent_id`,
  `agent_type`, `hook_event_name`.
- `SubagentStop` input adds `permission_mode`, `effort`, `stop_hook_active`,
  `agent_transcript_path` (the subagent's own transcript), `background_tasks` and
  `session_crons`.

## 5. `Stop` hook input

**Result.** `session_id`, `transcript_path`, `cwd`, `prompt_id`, `permission_mode`,
`effort`, `hook_event_name`, `stop_hook_active`, `last_assistant_message`,
`background_tasks`, `session_crons`.

**It can see live background tasks.** `background_tasks` is a list of
`{id, type, status, description, …}`. Observed types: `subagent` (with `agent_type`) and
`shell` (with `command`). A Monitor appears as `type: "shell"`, distinguishable only by its
description and command. The list also includes background shells started *by a
subagent*.

**Design consequence (adaptation).** The design had wakers tracked by hooks, with S0 to
decide which. The `Stop` hook alone is enough: at each stop it reads `background_tasks` and
`session_crons`. If either is non-empty, something will wake the agent, so the hook re-arms
the 45-minute cache-cold timer. If both are empty, it kills the timer. No
`SubagentStart`/`SubagentStop` bookkeeping is needed, which removes a state file that could
drift from the truth. `session_crons` was always empty in these tests; treating a
non-empty value as a waker is inferred, not tested.

## 6. Zenodo–GitHub integration (documentation lookup)

- GitHub's page "Referencing and citing content" still documents it: log in to Zenodo with
  GitHub, authorize, switch the repository on in Zenodo's GitHub settings, and "Zenodo
  archives your repository and issues a new DOI each time you create a new GitHub
  release." (docs.github.com/en/repositories/archiving-a-github-repository/referencing-and-citing-content)
- Zenodo reads `CITATION.cff`, a subset of its schema, for release metadata, **unless** a
  `.zenodo.json` exists: "Zenodo will only use the `.zenodo.json` metadata and ignore the
  `CITATION.cff` entirely." (help.zenodo.org/docs/github/describe-software/citation-file/)

**Design consequence.** The template ships `CITATION.cff` and no `.zenodo.json`. The
`zenodo-release` tool writes the DOI back to `CITATION.cff`. Code releases through the
GitHub integration and data releases through the API stay separate records.

## Summary of design changes

| # | change | reason |
|---|---|---|
| 1 | Waker detection uses only the `Stop` hook's `background_tasks` / `session_crons` | Test 5: the payload already lists live tasks |
| 2 | Plugin-internal references and resume prompts use full `open-science:` names | Test 2: unprefixed names resolve to same-named personal skills |
| 3 | Install docs say to retire or disable same-named personal skills | Test 2 |
| 4 | Jump resume logic tolerates repeated notices from one subagent | Test 3 caveat |
| 5 | Template ships `CITATION.cff` only, no `.zenodo.json` | Test 6 |

None of these results makes a core mechanism impossible.
