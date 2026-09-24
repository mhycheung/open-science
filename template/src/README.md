# Shared code

Code used by more than one task. Nothing under `src/` is an output path: a script here
takes its output directory as an argument. A script starts in its task directory and moves
here when a second task needs it. Changes are made on a worktree branch and merged.
