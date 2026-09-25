# Publish review rubric (tone and claims)

The deterministic checks have already passed. This review reads the diff since the last
publish (`publish/reports/<stem>.diff`) as a stranger would, and reports three kinds of
passages. It **writes a report; it never edits files and never blocks the publish by
itself**. The user decides.

Read only added lines (`+` in the diff). Quote each flagged passage exactly, with its file
and the line in the new file.

## 1. Tone: flag a passage that judges people rather than work

Flag:
- a judgement of a person or group: their competence, honesty, motives or character
  ("the authors clearly did not understand", "sloppy people", "they are hiding something");
- contempt or ridicule of someone's work, beyond saying what is wrong with it
  ("a joke of a paper", "embarrassingly bad", "garbage results");
- offensive words, and insults even when meant as a joke;
- private remarks: gossip, remarks about someone's career or personal life, the content of
  private conversations, emails or referee reports.

**Do not flag normal technical criticism.** Saying that a method, result or paper is wrong,
biased, limited or not reproducible is the work of science, and must be stated plainly:
- "Method X is biased in regime Y: the recovered amplitude is 20 % low (fig. 3)."
- "We could not reproduce Table 2 of [@smith2020]; our value differs by 3 sigma."
- "This approach fails for high spins and should not be used there."
- "The earlier analysis in t03 was wrong; t05 supersedes it."
- "The code of [@lee2019] has a sign error in eq. 7."

A test: would the sentence still be true, and still useful, if the author of the criticised
work read it? If yes, it is criticism of the work, and it is not flagged.

## 2. Claims: flag a result stated as settled without support

Flag:
- a result called done, confirmed, proven, established or verified in the text, when the
  document's node header says `status: active` or `verification: unverified`;
- a numerical result with no evidence pointer (provenance file, figure, script, or
  citation) anywhere in the same document;
- a statement that a check passed, with no record of the check.

Do not flag hedged statements ("preliminary", "so far", "we think", "not yet checked"),
plans, or results that point to their evidence.

## 3. Private material: flag what the export leaves out, restated

Material that is not exported is soft-private or hard-private (`AGENTS.md` §6). The
`private-content` check refuses exact copies of hard-private material: ids, titles, paths,
and runs of 12 words shared with a hard-private file. It does not catch a paraphrase, and it
only lists soft-private mentions in the report's notes. Read the report's "Excluded files"
list, open the excluded files whose topic touches the added lines (private tasks,
`private-docs/`, `brainstorm/`, notes outside the manifest), and flag:

- an added passage that restates hard-private content in other words: a result, a name, a
  collaboration, a plan or a number that appears only in hard-private material;
- a soft-private mention (listed in the notes) that is more than a mention in passing: a
  passage that restates the content of soft-private material, or depends on it to be
  understood.

A hard-private finding goes to the user with the choice to change the wording, remove it,
or redact it (skill step 3).

## Output

Append to the publish report, under "## Review (tone, claims)":

```
- [tone|claim|private] <file>:<line> "<exact quote>"
  why: <one sentence>
  suggestion: <a rewrite that keeps the technical content>
```

If nothing is flagged, write `No passages flagged (<n> added lines read).`
End with one line: the number of passages flagged in each category.
