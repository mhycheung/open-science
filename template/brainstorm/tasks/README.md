# Brainstorm tasks

One directory per idea that needs more than a few lines, `brainstorm/tasks/<id>/`, made with
the `open-science-project:new-task` skill or directly:

    opsci task new b01-<slug> --title "<title>" --root brainstorm

It has the same files as a project task (`context.md` with a node header, `log.md`,
`subcontext/`, and `plan.md` with `--plan`), and the same node-header schema (the project's
`tasks/README.md`). Its context file has the same 200-line cap. Its edges can only name
other brainstorm nodes.

An idea becomes project work through a new project task that restates it; see
`brainstorm/README.md`, "When an idea graduates".
