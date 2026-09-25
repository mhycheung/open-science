@AGENTS.md

# Claude Code only

The file above holds the instructions for every agent. This part holds what only Claude
Code uses.

## Skills

The framework's skills come from the open-science plugins. **Always name them in full**
(`open-science-project:new-task`): a skill with the same short name installed elsewhere may
otherwise be chosen instead.

- Starting a task: `open-science-project:new-task`.
- Keeping context files current: `open-science-project:context-files`. Main agents load it at
  session start.
<!-- opsci:context -->
- Session jumps: `open-science-context:context-management`. Main agents load it at session
  start, with `open-science-project:context-files`.
- Taking over work from a context file: `open-science-context:continue-context`.
- Questions about a context file, without acting on it: `open-science-context:advise-with-context`.
<!-- /opsci:context -->
- Publishing: `open-science-publish:publish`; data releases: `open-science-publish:zenodo-release`
  (plugin `open-science-publish`, if installed).
<!-- opsci:notion -->
- Notion (this project is mirrored there, `AGENTS.md` section 10):
  `open-science-project:notion`. Main agents load it at session start.
<!-- /opsci:notion -->

## Dispatch tiers

Subagent types are defined in `.claude/agents/`. Every dispatch names one explicitly.

| tier | use for |
|---|---|
| `med-effort` | the default: implement a specified change, run a specified sweep, review a diff, analyse a result |
| `high-effort` | only after a `med-effort` attempt at the same task has provably failed, with the failure recorded |
| `low-effort` | fully specified mechanics with no decision left: run a given command, a mechanical edit, pull numbers from a file |
| `literature` | read a source and judge it; find which section supports a claim |
| `text` | mechanical work on text: find a string, extract a table, assemble a document |

The model behind each tier is set in its file; change it to the models you have.

<!-- opsci:context -->
## Sessions

Agents clear their own conversation and resume from the context files ("jumps"); this is
expected. The `open-science-context:context-management` skill describes when and how.
<!-- /opsci:context -->
