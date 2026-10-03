# What the public pages show

## What is not science or code

Every exported Markdown file is published, while the private files keep everything. The
public record holds the science and the code; what that covers, for whole tasks and for
passages, is in `science-or-code.md`. In the context files, housekeeping items in "Waiting
on the user", "Next step" and "Open questions" are left out too: "redo the plot?", "commit
the plots?", "which file name?", "whether to commit `lit_cache/2609.07873/` (28 MB,
untracked)".

Keep what a reader of the project needs: a scientific decision the user owes ("report
$\iota$ at a single $t_*$, or the range over the three peaks?"), the choice of the next
task, "next: compute $X$ and add it to the task goal".

Wrap each such passage in the private source file:

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

Tabs: Home (`README.md`, or the abstract and the write-up, below); Write-up (`WRITEUP.md`,
once filled in); Results ("Main results", the milestone page, then each result
page grouped by task); Map (the logic of the project, the claims graph and the project
graph on one page); Dead ends; Tasks (an overview, then one page per task with its
context, results, figures with their captions, plan, map, working notes and log);
Citations; Context; Log (every month's entries grouped by date, newest first, task ids
linked); About (`README.md`, when Home shows the abstract).

`ABSTRACT.md` and `WRITEUP.md` are written by the user and hold only `TODO` until then (a
title and `<!-- ... -->` comments do not count). Once `ABSTRACT.md` is filled in, Home is the
project title, the abstract and, if filled in, the write-up, and `README.md` moves to the
About tab. A file that still says `TODO` is not on the site.

Other markdown files (`AGENTS.md`, `PROJECT.md`, `rules/`, `docs/`, `src/README.md`) are not
pages; a link to one becomes plain text. `Not verified` and `unverified` labels are red and
bold.
