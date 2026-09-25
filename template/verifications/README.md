# Verifications

Verification tasks that check the work of more than one task: audits, checks, reproductions
and adverse reviews of work that is already done. A verification of one task's work goes
inside that task instead, in `tasks/<id>/verifications/<vid>/`.

Each directory here is a task, `verifications/<vid>/`, with the same files as any other
task (`context.md` with a node header, `map.md`, `results/`, `log.md`, `subcontext/`, and
`plan.md` if it has a plan). Make one with the `open-science-project:new-task` skill or
directly:

    opsci task new v01-<slug> --title "<title>" --verifies <id> <id> ...

`opsci task new` puts it here or in the task, from the ids in `--verifies`. Its header
names the nodes it checks in `verifies:`. The graphs draw it as a hexagon, labelled
`verification`, with a dotted arrow to each node it verifies. Its privacy is by default the
strictest privacy of those nodes. Ids start with `v` and a number, so that they are never
confused with project task ids. See `tasks/README.md`, "Verification tasks".
