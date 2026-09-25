"""Publish screening of what the export leaves out: the map rebuilt from the published nodes,
the `references`, `private-content` and `redaction` checks, and the three privacy tiers.
Each refusal case has a control."""
import pytest

from opsci import publish, tasks
from test_publish import commit, problems, proj, set_header  # noqa: F401  (proj is a fixture)

SECRET_TITLE = "Secret collaboration with Rivendell"


def of(probs, check):
    return [str(p) for p in probs if p.check == check]


def add_secret(root, depends=True, privacy="hard-private"):
    """The probe of 2026-09-24: a private task, and a published task that depends on it."""
    tasks.new_task(root, "secret-collab", SECRET_TITLE, summary="Unpublished joint analysis.",
                   privacy=privacy)
    tasks.new_task(root, "open-work", "Open work", summary="Published.",
                   depends_on=["secret-collab"] if depends else ())


def notes_of(root):
    _, ex = problems(root)
    return publish.run_checks(root, ex)[1]["notes"]


def add_hard_private(root, path):
    m = root / "publish/manifest.yaml"
    m.write_text(m.read_text().replace("never:\n", f"hard_private:\n  - {path}\nnever:\n"))


def append(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(text)


def test_exported_map_leaves_out_private_nodes(proj):
    add_secret(proj)
    commit(proj)
    committed = (proj / "map/graph.md").read_text()
    assert "secret-collab" in committed  # the private map covers every node
    probs, ex = problems(proj)
    graph = (ex.tree / "map/graph.md").read_text()
    assert "open-work" in graph and "t01-fit" in graph
    for leak in ("secret-collab", "secret_collab", "Rivendell", "Unpublished joint analysis"):
        assert leak not in graph, leak
    assert ex.rebuilt_map == ["map/graph.md", "map/dead_ends.md"]
    assert "tasks/secret-collab/context.md" not in ex.files
    # the published task still names its hard-private dependency in its header: refused twice
    assert of(probs, "references") == [
        "[references] tasks/open-work/context.md: `depends_on` names node 'secret-collab', which is "
        "hard-private: remove the reference"]
    assert any("secret-collab" in p for p in of(probs, "private-content"))


def test_soft_private_dependency_is_shown_unlinked(proj):
    add_secret(proj, privacy="soft-private")
    commit(proj)
    probs, ex = problems(proj)
    assert probs == [], [str(p) for p in probs]
    graph = (ex.tree / "map/graph.md").read_text()
    assert "n_secret_collab --> n_open_work" in graph and SECRET_TITLE in graph
    assert "| secret-collab (not published) |" in graph and "../tasks/secret-collab" not in graph
    assert "[open-work](../tasks/open-work/context.md)" in graph  # control: published, linked
    assert "tasks/secret-collab/context.md" not in ex.files
    assert any(n.startswith("soft-private material is mentioned") and "secret-collab" in n
               for n in notes_of(proj))


def test_private_task_without_references_passes(proj):
    add_secret(proj, depends=False)
    commit(proj)
    probs, ex = problems(proj)
    assert probs == [], [str(p) for p in probs]
    assert "secret-collab" not in (ex.tree / "map/graph.md").read_text()
    _, info = publish.run_checks(proj, ex)
    assert any("rebuilt from the 2 nodes that are not hard-private, 2 of them published" in n
               for n in info["notes"])
    assert any(n.startswith("private-content: compared") for n in info["notes"])


@pytest.mark.parametrize("privacy,shown", [("soft-private", True), ("hard-private", False)])
def test_dead_ends_and_private_superseder(proj, privacy, shown):
    tasks.new_task(proj, "old-way", "Old way", summary="Tried first.")
    set_header(proj / "tasks/old-way/context.md", "status", "superseded")
    tasks.new_task(proj, "new-way-private", "New way", summary="Replaces it.", supersedes=["old-way"],
                   privacy=privacy)
    commit(proj)
    probs, ex = problems(proj)
    dead = (ex.tree / "map/dead_ends.md").read_text()
    assert "old-way" in dead and ("new-way-private" in dead) == shown
    assert "new-way-private" in (proj / "map/dead_ends.md").read_text()  # control: private map has it
    assert probs == [], [str(p) for p in probs]  # the private node names the public one, not the reverse


@pytest.mark.parametrize("privacy,msg", [
    ("soft-private", "which is not exported (soft-private): make it a plain mention"),
    ("hard-private", "which is hard-private: remove the link and the mention")])
def test_link_to_unexported_file_refused(proj, privacy, msg):
    tasks.new_task(proj, "t02-hidden", "Hidden", summary="Not published.", privacy=privacy)
    append(proj / "tasks/t01-fit/context.md", "\nDetails in [the notes](../t02-hidden/log.md).\n")
    commit(proj)
    refs = of(problems(proj)[0], "references")
    assert len(refs) == 1 and f"links to 'tasks/t02-hidden/log.md', {msg}" in refs[0], refs


def test_link_to_unexported_directory_refused(proj):
    append(proj / "private-docs/meeting.md", "Private meeting notes.\n")
    append(proj / "README.md", "\nSee [private](private-docs/) and [site](<https://example.org/x>).\n")
    commit(proj)
    refs = of(problems(proj)[0], "references")
    assert len(refs) == 1 and "links to 'private-docs'" in refs[0]


def test_links_to_exported_and_external_targets_pass(proj):
    append(proj / "README.md", "\nSee [the fit](tasks/t01-fit/context.md#goal), [PROJECT](./PROJECT.md), "
                               "[web](https://example.org), [top](#top), [missing](nowhere.md) and "
                               "`[code](private-docs/)`.\n\n[ref]: tasks/t01-fit/log.md\n")
    commit(proj)
    probs, _ = problems(proj)
    assert probs == [], [str(p) for p in probs]


def test_hard_private_node_id_in_text_refused(proj):
    add_secret(proj, depends=False)
    append(proj / "tasks/open-work/context.md", "\nThis builds on secret-collab.\n")
    commit(proj)
    pc = of(problems(proj)[0], "private-content")
    assert len(pc) == 1 and "names the hard-private node id 'secret-collab'" in pc[0]


def test_soft_private_node_id_in_text_is_noted(proj):
    add_secret(proj, depends=False, privacy="soft-private")
    append(proj / "tasks/open-work/context.md", "\nThis builds on secret-collab.\n")
    commit(proj)
    assert problems(proj)[0] == []
    assert any("tasks/open-work/context.md:" in n and "node id 'secret-collab'" in n for n in notes_of(proj))


def test_hard_private_title_in_text_refused(proj):
    add_secret(proj, depends=False)
    append(proj / "README.md", "\nWith thanks to the secret collaboration  with\nRivendell team.\n")
    commit(proj)
    pc = of(problems(proj)[0], "private-content")
    assert len(pc) == 1 and "title of a hard-private node" in pc[0]


def test_short_private_title_is_not_matched(proj):
    tasks.new_task(proj, "t02-hidden", "Hidden work", summary="Not published.", privacy="hard-private")
    append(proj / "README.md", "\nNo hidden work here.\n")
    commit(proj)
    assert of(problems(proj)[0], "private-content") == []


def test_private_docs_path_is_soft_hard_private_path_refused(proj):
    append(proj / "private-docs/meeting-notes.md", "Private meeting notes.\n")
    append(proj / "private-docs/contract.md", "Terms.\n")
    append(proj / "README.md", "\nSee `private-docs/meeting-notes.md` and `private-docs/`.\n")
    commit(proj)
    assert problems(proj)[0] == []  # private-docs/ is soft-private: a mention is allowed
    assert any("path 'private-docs/meeting-notes.md'" in n for n in notes_of(proj))
    add_hard_private(proj, "private-docs/contract.md")
    append(proj / "README.md", "\nAnd `private-docs/contract.md`.\n")
    commit(proj)
    pc = of(problems(proj)[0], "private-content")
    assert len(pc) == 1 and "hard-private path 'private-docs/contract.md'" in pc[0], pc


def test_hard_private_list_beats_include(proj):
    append(proj / "rules/R09-vendor.md", "Vendor terms.\n")
    add_hard_private(proj, "rules/R09-*.md")
    commit(proj)
    probs, ex = problems(proj)
    assert "rules/R09-vendor.md" not in ex.files and "rules/R09-vendor.md" in ex.hard
    assert ex.excluded["rules/R09-vendor.md"] == "listed under hard_private: hard-private"
    assert "rules/README.md" in ex.files  # control


@pytest.mark.parametrize("privacy,refused", [("public", False), ("hard-private", True)])
def test_brainstorm_node_id(proj, privacy, refused):
    """brainstorm/ is soft-private as a directory; a hard-private brainstorm task is not."""
    append(proj / "brainstorm/tasks/idea-x/context.md",
           f"---\nid: idea-x\ntitle: Idea\ntype: task\nstatus: active\nprivacy: {privacy}\n"
           "summary: An idea.\n---\n")
    append(proj / "tasks/t01-fit/context.md", "\nThe fit came from idea-x.\n")
    commit(proj)
    pc = of(problems(proj)[0], "private-content")
    assert bool(pc) == refused and all("hard-private node id 'idea-x'" in p for p in pc), pc


SENTENCE = ("the calibration offset was measured at the second site by our partner group "
            "before the agreement")


def test_shared_run_with_hard_private_file_refused(proj):
    append(proj / "private-docs/notes.md", f"Meeting. {SENTENCE}. More.\n")
    add_hard_private(proj, "private-docs/notes.md")
    words = SENTENCE.split()
    append(proj / "README.md", "\n" + " ".join(words[:11]) + " and so on.\n")  # 11 words: control
    commit(proj)
    assert of(problems(proj)[0], "private-content") == []
    append(proj / "README.md", "\n" + " ".join(words[:12]) + ".\n")  # 12 words
    commit(proj)
    pc = of(problems(proj)[0], "private-content")
    assert len(pc) == 1 and "shares 1 run(s) of 12 words with hard-private private-docs/notes.md" in pc[0]


def test_shared_run_with_soft_private_file_is_noted(proj):
    append(proj / "private-docs/notes.md", f"Meeting. {SENTENCE}. More.\n")
    append(proj / "README.md", "\n" + SENTENCE + ".\n")
    commit(proj)
    assert problems(proj)[0] == []
    assert any("README.md shares 5 run(s) of 12 words with private-docs/notes.md" in n for n in notes_of(proj))


def test_task_skeleton_is_not_a_shared_run(proj):
    for i in range(3):
        tasks.new_task(proj, f"t0{i + 2}-private", f"Private {i}", summary="Not published.", plan=True,
                       privacy="hard-private")
    tasks.new_task(proj, "t09-public", "Public", summary="Published.", plan=True)
    commit(proj)
    probs, _ = problems(proj)
    assert probs == [], [str(p) for p in probs]


def test_docs_need_no_status_header(proj):
    m = proj / "publish/manifest.yaml"
    m.write_text(m.read_text().replace("  - path: tasks\n", "  - path: tasks\n  - path: docs\n  - path: results\n"))
    append(proj / "docs/guide.md", "# Guide\n\nHow to run the fit.\n")
    append(proj / "results/fit.md", "# Fit\n\nNo header.\n")  # control: still needs one
    commit(proj)
    assert [str(p) for p in problems(proj)[0]] == [
        "[status] results/fit.md: no `status:` in a front-matter header"]


def test_published_brainstorm_honours_task_headers(proj):
    from opsci import layout
    m = proj / "publish/manifest.yaml"
    m.write_text(m.read_text().replace("  - path: tasks\n", "  - path: tasks\n  - path: brainstorm\n"))
    tasks.new_task(proj / "brainstorm", "idea-open", "Open idea", summary="Shared.", privacy="public")
    tasks.new_task(proj / "brainstorm", "idea-soft", "Soft idea", summary="Named only.")  # default tier
    tasks.new_task(proj / "brainstorm", "idea-closed", "Closed idea about Rivendell", summary="Private.",
                   privacy="hard-private")
    commit(proj)
    probs, ex = problems(proj)
    assert "brainstorm/tasks/idea-open/log.md" in ex.files  # control
    assert not [f for f in ex.files if "idea-closed" in f or "idea-soft" in f]
    for rel, link in (("brainstorm/map/graph.md", "../tasks/idea-open/context.md"),
                      ("map/graph.md", "../brainstorm/tasks/idea-open/context.md")):
        graph = (ex.tree / rel).read_text()
        assert f"[idea-open]({link})" in graph and "| idea-soft (not published) |" in graph, rel
        assert "idea-closed" not in graph and "Rivendell" not in graph, rel
    assert probs == [], [str(p) for p in probs]
    assert layout.outdated_message(proj) is None


def test_old_layout_is_noted_in_the_report(proj):
    fw = proj / "config/framework.yaml"
    fw.write_text("\n".join(l for l in fw.read_text().splitlines() if not l.startswith("layout_version")) + "\n")
    commit(proj)
    _, report, _ = publish.check(proj)
    assert "older than the framework's" in report.read_text()


# ------------------------------------------------------------------ redaction

def test_redaction_replaces_the_span_in_the_export_only(proj):
    add_secret(proj, depends=False)
    ctx = proj / "tasks/t01-fit/context.md"
    append(ctx, "\nWe calibrated with <!-- redact: proprietary data -->the Acme spectra from\n"
                "secret-collab<!-- /redact --> and the public set.\n")
    commit(proj)
    probs, ex = problems(proj)
    assert probs == [], [str(p) for p in probs]
    out = (ex.tree / "tasks/t01-fit/context.md").read_text()
    assert "We calibrated with [redacted (proprietary data)] and the public set." in out
    assert "Acme" not in out and "secret-collab" not in out
    assert "the Acme spectra" in ctx.read_text()  # the private file keeps the text
    assert ex.redacted == {"tasks/t01-fit/context.md": 1}
    assert any(n.startswith("redaction: 1 span(s) redacted in tasks/t01-fit/context.md") for n in notes_of(proj))


def test_hard_private_mention_outside_the_marker_is_still_refused(proj):
    add_secret(proj, depends=False)
    append(proj / "tasks/t01-fit/context.md",
           "\n<!-- redact: private information -->secret-collab<!-- /redact --> and secret-collab.\n")
    commit(proj)
    pc = of(problems(proj)[0], "private-content")
    assert len(pc) == 1 and "secret-collab" in pc[0]


@pytest.mark.parametrize("text,msg", [
    ("<!-- redact: proprietary data -->Acme spectra, never closed.", "not closed"),
    ("<!-- redact: -->Acme<!-- /redact -->", "without a reason"),
    ("Acme<!-- /redact -->", "not closed")])
def test_bad_redaction_marker_refused(proj, text, msg):
    append(proj / "tasks/t01-fit/context.md", "\n" + text + "\n")
    commit(proj)
    rd = of(problems(proj)[0], "redaction")
    assert len(rd) == 1 and msg in rd[0], rd


def test_redaction_in_a_node_header(proj):
    ctx = proj / "tasks/t01-fit/context.md"
    ctx.write_text(ctx.read_text().replace(
        "summary: Fits the model.",
        "summary: '<!-- redact: proprietary data -->Acme<!-- /redact --> fit of the model.'"))
    commit(proj)
    probs, ex = problems(proj)
    assert probs == [], [str(p) for p in probs]
    graph = (ex.tree / "map/graph.md").read_text()
    assert "[redacted (proprietary data)] fit of the model." in graph and "Acme" not in graph
    # unquoted: valid YAML before redaction ("redact:" with no space), not after: refused
    ctx.write_text(ctx.read_text().replace(
        "summary: '<!-- redact: proprietary data -->Acme<!-- /redact --> fit of the model.'",
        "summary: <!--redact:proprietary data-->Acme<!--/redact--> fit of the model."))
    commit(proj)
    probs = problems(proj)[0]
    rd = of(probs, "redaction")
    assert len(rd) == 1 and "put the redacted value in quotes" in rd[0], [str(p) for p in probs]
    assert {p.check for p in probs} == {"redaction", "status"}  # status reads the broken header too


# ------------------------------------------------------------------ the old `publish` field

def test_old_publish_field_and_policy_are_refused(proj):
    from opsci import nodes
    set_header(proj / "tasks/t01-fit/context.md", "publish", "yes")
    errs = [str(e) for e in nodes.scan(proj).errors]
    assert len(errs) == 1 and "`publish` was replaced by `privacy" in errs[0]
    m = proj / "publish/manifest.yaml"
    m.write_text(m.read_text().replace("default_privacy: public", 'embargo_default: "no"'))
    with pytest.raises(publish.PublishError, match="replaced by policy.default_privacy"):
        publish.load_manifest(proj)


# ------------------------------------------------------------------ unpublished brainstorm in the map

def test_unpublished_brainstorm_is_named_in_the_project_map(proj):
    """brainstorm/ is not in the manifest: no file is exported, but its soft-private nodes are
    named in the public project map, in their box; hard-private ones are not."""
    tasks.new_task(proj / "brainstorm", "idea-soft", "Soft idea", summary="Named only.",
                   depends_on=["t01-fit"])
    tasks.new_task(proj / "brainstorm", "idea-closed", "Closed idea about Rivendell", summary="Private.",
                   privacy="hard-private")
    commit(proj)
    probs, ex = problems(proj)
    assert probs == [], [str(p) for p in probs]
    assert not [f for f in ex.files if f.startswith("brainstorm/")]
    graph = (ex.tree / "map/graph.md").read_text()
    assert 'subgraph brainstorm["brainstorm"]' in graph and "n_t01_fit --> n_idea_soft" in graph
    assert "| idea-soft (not published) |" in graph
    assert "idea-closed" not in graph and "Rivendell" not in graph


# ------------------------------------------------------------------ map overrides

OVERRIDES = """\
groups:
  - id: private-calibration
    title: Private calibration work
    summary: Two private studies of the calibration.
    members: [cal-a, cal-b]
nodes:
  cal-c:
    title: A private cross-check
    summary: A cross-check of the fit.
"""


def add_calibration(root):
    tasks.new_task(root, "cal-a", "Acme detector gain at 3.2 kV", summary="Gain curve from the Acme run.",
                   depends_on=["t01-fit"], privacy="soft-private")
    tasks.new_task(root, "cal-b", "Acme gain drift in March", summary="Drift of the Acme gain.",
                   depends_on=["cal-a"], privacy="soft-private")
    tasks.new_task(root, "cal-c", "Fit residuals against Acme temperature log", summary="Residuals.",
                   privacy="soft-private")
    tasks.new_task(root, "open-work", "Open work", summary="Published.", depends_on=["cal-b"])


def test_map_overrides_group_and_rewrite_unpublished_nodes(proj):
    add_calibration(proj)
    commit(proj)
    graph = (problems(proj)[1].tree / "map/graph.md").read_text()
    assert "Acme" in graph  # control: without overrides the headers are shown as they are
    (proj / "publish/map_overrides.yaml").write_text(OVERRIDES)
    commit(proj)
    probs, ex = problems(proj)
    assert probs == [], [str(p) for p in probs]
    graph = (ex.tree / "map/graph.md").read_text()
    for gone in ("Acme", "cal-a", "cal-b", "n_cal_a", "Residuals"):
        assert gone not in graph, gone
    assert "| private-calibration (not published) |" in graph and "Private calibration work" in graph
    # edges to members point to the group; the edge between members is gone
    assert "n_t01_fit --> n_private_calibration" in graph and "n_private_calibration --> n_open_work" in graph
    assert "n_private_calibration --> n_private_calibration" not in graph
    assert "| cal-c (not published) |" in graph and "A private cross-check" in graph
    assert "publish/map_overrides.yaml" not in ex.files
    _, report, _ = publish.check(proj)
    text = report.read_text()
    assert "## Unpublished nodes in the public map" in text and "`cal-a`: in group `private-calibration`" in text


@pytest.mark.parametrize("text,msg", [
    ("groups:\n  - {id: g, title: G, summary: S, members: [cal-a, open-work]}\n", "member 'open-work' is published"),
    ("groups:\n  - {id: g, title: G, summary: S, members: [cal-a]}\n", "at least two"),
    ("groups:\n  - {id: t01-fit, title: G, summary: S, members: [cal-a, cal-b]}\n", "already used"),
    ("groups:\n  - {id: g, title: G, summary: S, members: [cal-a, nosuch]}\n", "'nosuch' is not a node"),
    ("nodes:\n  cal-c: {title: T}\n  t01-fit: {title: T, summary: S}\n", "'t01-fit' is published"),
    ("nodes:\n  cal-c: {title: T, summary: ''}\n", "`summary` must be one non-empty line"),
    ("group: []\n", "unknown key 'group'")])
def test_bad_map_overrides_refused(proj, text, msg):
    add_calibration(proj)
    (proj / "publish/map_overrides.yaml").write_text(text)
    commit(proj)
    mo = of(problems(proj)[0], "map-overrides")
    assert len(mo) == 1 and msg in mo[0], mo
