# What the public pages show

## Housekeeping in the context files

The project `context.md` and every public task `context.md` are published, while the
private files keep everything. Items in "Waiting on the user", "Next step" and "Open
questions" that are housekeeping for the user are not published:

- "redo the plot?", "commit the plots?", "which file name?";
- "whether to commit `lit_cache/2609.07873/` (28 MB, untracked)";
- "the owner has not answered on Slack yet".

Keep what a reader of the project needs: a scientific decision the user owes ("report
$\iota$ at a single $t_*$, or the range over the three peaks?"), the choice of the next
task, "next: compute $X$ and add it to the task goal".

Wrap each housekeeping item in the private source file:

```
<!-- omit -->- Whether to commit the new figures?<!-- /omit -->
```

The export removes the span with its markers, and the lines it filled; a `## ` section that
it leaves empty loses its heading. The `omission` check refuses a marker that is not closed.
The report notes count the omitted spans per file. Unlike a redaction, an omission leaves
no trace and needs no decision from the user beforehand; list each one at the approval.

## Citations

`citations/consulted.md` is soft-private: never exported, whatever the manifest says. It may
be mentioned by name, not linked.

The Citations page is a table made from `citations/used.bib`. Each row has the reference in
journal style, `B. P. Abbott et al. (LIGO Scientific, Virgo), Phys. Rev. D 93, 122003 (2016),
arXiv:1602.03839 [gr-qc].`, with the journal reference linked to the DOI (or `url`) and the
arXiv number to arXiv, and the entry's `usage` field: a few words on how the project uses
the work. Without `usage` the row lists the results whose `uses:` name the key. What an
entry needs:

| field | for |
|---|---|
| `author`, `collaboration` | the author list: the first author and "et al." beyond three |
| `journal`, `volume`, `pages`, `year` | the journal reference (without `journal`, the title is shown) |
| `doi` or `url` | the link of the journal reference or title |
| `eprint`, `archivePrefix = {arXiv}`, `primaryClass` | the arXiv link, e.g. `arXiv:1602.03839 [gr-qc]` |
| `usage` | how the project uses the work, one short sentence |

`[@key]` in a page links to the key's row.

## The site

Tabs: Home (`README.md`); Results ("Main results", the milestone page, then each result
page grouped by task); Map (the logic of the project, the claims graph and the project
graph on one page); Dead ends; Tasks (an overview, then one page per task with its
context, results, figures with their captions, plan, map, working notes and log);
Citations; Context; Log (every month's entries grouped by date, newest first, task ids
linked). Other markdown files (`AGENTS.md`, `PROJECT.md`, `rules/`, `docs/`,
`src/README.md`) are not pages; a link to one becomes plain text. `Not verified` and
`unverified` labels are red and bold.
