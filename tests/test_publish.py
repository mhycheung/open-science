# opsci: planted-leaks (this file plants leaks and secrets as refusal cases)
"""Publish filter and private <-> public sync (the "publish filter" and "sync" test rows)."""
import json
import re
import subprocess

import pytest

from conftest import TEMPLATE, git
from opsci import mapbuild, publish, tasks, template

USER = ["-c", "user.name=User", "-c", "user.email=user@example.org"]
AGENT_TRAILER = "\n\nClaude-Session: https://claude.ai/code/session_x"


def commit(root, msg="change", agent=False):
    mapbuild.build(root)
    git(root, "add", "-A")
    git(root, *USER, "commit", "-qm", msg + (AGENT_TRAILER if agent else ""), "--allow-empty")


def set_header(path, key, value):
    text = path.read_text()
    if re.search(rf"^{key}:", text, re.M):
        text = re.sub(rf"^{key}:.*$", f"{key}: {value}", text, count=1, flags=re.M)
    else:
        text = text.replace("\nsummary:", f"\n{key}: {value}\nsummary:", 1)
    path.write_text(text)


@pytest.fixture
def proj(tmp_path):
    root = template.instantiate(tmp_path / "proj", "demo", "Demo project", "A. Person", template=TEMPLATE)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "User")
    git(root, "config", "user.email", "user@example.org")
    tasks.new_task(root, "t01-fit", "Fit the model", summary="Fits the model.")
    with open(root / "tasks/t01-fit/context.md", "a") as f:
        f.write("\nThe fit follows [@smith2020].\n")
    with open(root / "citations/used.bib", "a") as f:
        f.write("@article{smith2020,\n  title={A},\n  author={Smith},\n  year={2020}\n}\n")
    m = root / "publish/manifest.yaml"
    m.write_text(m.read_text().replace("collaborators_agreed: false", "collaborators_agreed: true"))
    commit(root, "init")
    return root


def problems(root):
    ex = publish.export(root, root.parent / f"ex{len(list(root.parent.glob('ex*')))}")
    probs, _ = publish.run_checks(root, ex)
    return probs, ex


def checks_of(root):
    return sorted({p.check for p in problems(root)[0]})


def test_clean_project_passes(proj):
    probs, ex = problems(proj)
    assert probs == [], [str(p) for p in probs]
    assert "tasks/t01-fit/context.md" in ex.files
    assert "PROJECT.md" in ex.files  # exported, and needs no status header
    n, report, _ = publish.check(proj)
    assert n == 0 and "PASSED" in report.read_text()


def test_export_follows_manifest_and_headers(proj):
    (proj / "notes").mkdir()
    (proj / "notes/private.md").write_text("private notes\n")  # outside the manifest
    tasks.new_task(proj, "t02-hidden", "Hidden", summary="Not published.", privacy="soft-private")
    tasks.new_task(proj, "t03-secret", "Secret", summary="Later.", privacy="hard-private")
    (proj / "tasks/t01-fit/draft.md").write_text(
        "---\nid: t01-draft\ntitle: Draft\ntype: result\nstatus: active\nprivacy: soft-private\nsummary: x\n---\n")
    (proj / "lit_cache/paper.txt").write_text("someone else's text\n")
    m = proj / "publish/manifest.yaml"
    m.write_text(m.read_text().replace("  - path: tasks\n", "  - path: tasks\n  - path: lit_cache\n  - path: rules/secret.md\n")
                 .replace("never:\n", "never:\n  - rules/secret.md\n"))
    (proj / "rules/secret.md").write_text("x\n")
    commit(proj)
    ex = problems(proj)[1]
    for f in ("notes/private.md", "tasks/t02-hidden/context.md", "tasks/t03-secret/context.md",
              "tasks/t01-fit/draft.md", "lit_cache/paper.txt", "rules/secret.md",
              "publish/PRIVATE_POLICY.md", "publish/manifest.yaml"):
        assert f not in ex.files, f
    assert ex.excluded["notes/private.md"] == "not in the manifest"
    assert ex.excluded["tasks/t02-hidden/context.md"] == "task t02-hidden: soft-private"
    assert ex.excluded["tasks/t03-secret/context.md"] == "task t03-secret: hard-private"
    assert ex.excluded["tasks/t01-fit/draft.md"] == "node t01-draft: soft-private"
    assert {f for f in ex.hard if f.startswith("tasks/")} == {
        "tasks/t03-secret/context.md", "tasks/t03-secret/log.md", "tasks/t03-secret/map.md",
        "tasks/t03-secret/results/README.md", "tasks/t03-secret/subcontext/README.md"}
    assert ex.excluded["rules/secret.md"] == "listed under never"


def test_export_is_of_the_commit_not_the_working_tree(proj):
    before = problems(proj)[1].export_id
    (proj / "tasks/t01-fit/context.md").write_text("uncommitted edit\n")
    assert problems(proj)[1].export_id == before
    git(proj, "checkout", "--", ".")
    with open(proj / "tasks/t01-fit/context.md", "a") as f:
        f.write("more\n")
    commit(proj)
    assert problems(proj)[1].export_id != before


PLANTED = [
    ("Our output is in /scratch/grp/run1/out.h5.", "leak"),
    ("Contact real.person@univ.edu.", "leak"),
    ("SLACK=" + "xox" + "b-1234567890-abcdefghijkl", "secret"),
    ("ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8", "secret"),
    ("As shown by [@nobody1999].", "citation"),
]


@pytest.mark.parametrize("text,check", PLANTED)
def test_planted_content_is_refused(proj, text, check):
    with open(proj / "tasks/t01-fit/context.md", "a") as f:
        f.write(text + "\n")
    commit(proj)
    assert check in checks_of(proj)


def test_site_identifier_and_private_policy_are_refused(proj):
    (proj / "config/site.local.yaml").write_text("identifiers: [Rivendell]\n")  # git-ignored
    pol = proj / "publish/PRIVATE_POLICY.md"
    pol.write_text(pol.read_text().replace("```\n```", "```\nProject\\s+Nightjar\n```"))
    with open(proj / "tasks/t01-fit/context.md", "a") as f:
        f.write("Run on Rivendell for Project Nightjar.\n")
    commit(proj)
    msgs = [p.message for p in problems(proj)[0] if p.check == "leak"]
    assert any("site.local.yaml" in m for m in msgs) and any("private-policy" in m for m in msgs)


def test_document_without_status_is_refused(proj):
    (proj / "results").mkdir(exist_ok=True)  # map build writes results/README.md
    (proj / "results/fit.md").write_text("# Fit result\n\nNo header.\n")
    m = proj / "publish/manifest.yaml"
    m.write_text(m.read_text().replace("  - path: tasks\n", "  - path: tasks\n  - path: results\n"))
    commit(proj)
    assert [p.path for p in problems(proj)[0]] == ["results/fit.md"]
    (proj / "results/fit.md").write_text("---\nstatus: done\n---\n# Fit result\n")
    commit(proj)
    assert problems(proj)[0] == []
    # plot captions need no header; the manifest's status_exempt adds to the defaults
    (proj / "results/fit_2026-09-25.caption.md").write_text("The fit.\n")
    (proj / "results/notes.md").write_text("Notes.\n")
    commit(proj)
    assert [p.path for p in problems(proj)[0]] == ["results/notes.md"]
    m.write_text(m.read_text() + "status_exempt:\n  - results/notes.md\n")
    commit(proj)
    assert problems(proj)[0] == []


def test_copyright_checks(proj):
    m = proj / "publish/manifest.yaml"
    m.write_text(m.read_text().replace("  - path: tasks\n", "  - path: tasks\n  - path: paper\n"))
    (proj / "paper").mkdir()
    (proj / "paper/other.pdf").write_bytes(b"%PDF-1.4 someone else's paper")
    commit(proj)
    assert checks_of(proj) == ["copyright"]
    # a one-page PDF is a figure
    (proj / "paper/other.pdf").write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Page >>\nendobj\n")
    commit(proj)
    assert problems(proj)[0] == []
    (proj / "paper/other.pdf").write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Page >>\nendobj\n"
                                           b"2 0 obj\n<< /Type /Page >>\nendobj\n")
    commit(proj)
    assert checks_of(proj) == ["copyright"]
    # the project's own paper, covered by a paper node, may be published
    (proj / "paper/node.yaml").write_text("id: p01\ntitle: Our paper\ntype: paper\nstatus: active\n"
                                          "privacy: public\nsummary: Our paper.\n")
    commit(proj)
    assert problems(proj)[0] == []
    # a long quotation
    quote = "> " + " ".join(["word"] * 160) + "\n"
    with open(proj / "tasks/t01-fit/context.md", "a") as f:
        f.write("\n" + quote)
    commit(proj)
    assert "150" in " ".join(p.message for p in problems(proj)[0])
    git(proj, "reset", "-q", "--hard", "HEAD~1")
    # text shared with a cached source
    source = " ".join(f"w{i}" for i in range(60))
    (proj / "lit_cache/src.txt").write_text(source + "\n")
    with open(proj / "tasks/t01-fit/context.md", "a") as f:
        f.write("\n" + " ".join(f"w{i}" for i in range(5, 50)) + "\n")
    commit(proj)
    assert [p.check for p in problems(proj)[0]] == ["copyright"]


def test_human_verified_by_agent_commit_is_refused(proj):
    (proj / "tasks/t01-fit/provenance.yaml").write_text("command: fit\n")
    ctx = proj / "tasks/t01-fit/context.md"
    set_header(ctx, "evidence", "tasks/t01-fit/provenance.yaml")
    set_header(ctx, "verification", "human-verified")
    commit(proj, "agent sets it", agent=True)
    assert checks_of(proj) == ["human-verified"]
    # the user setting it, in a commit of their own, passes
    set_header(ctx, "verification", "verified")
    commit(proj, "agent", agent=True)
    set_header(ctx, "verification", "human-verified")
    commit(proj, "user checked the fit")
    probs, _ = problems(proj)
    assert probs == []


def test_policy_and_stale_map_are_refused(proj):
    m = proj / "publish/manifest.yaml"
    m.write_text(m.read_text().replace("collaborators_agreed: true", "collaborators_agreed: false"))
    git(proj, "add", "-A")
    git(proj, *USER, "commit", "-qm", "x")
    assert checks_of(proj) == ["policy"]
    git(proj, "reset", "-q", "--hard", "HEAD~1")
    set_header(proj / "tasks/t01-fit/context.md", "status", "done")
    git(proj, "add", "-A")
    git(proj, *USER, "commit", "-qm", "no map build")
    assert checks_of(proj) == ["map"]


def test_symlink_is_refused(proj):
    (proj / "tasks/t01-fit/link.md").symlink_to("../../context.md")
    commit(proj)
    with pytest.raises(publish.PublishError, match="symbolic link"):
        problems(proj)


# ------------------------------------------------------------------ private <-> public


@pytest.fixture
def public(tmp_path):
    bare = tmp_path / "public.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    return bare


def publish_now(root, bare):
    n, _, ex = publish.check(root)
    assert n == 0
    return publish.push(root, ex.export_id, str(bare))


def public_edit(tmp_path, bare, path, text):
    clone = tmp_path / f"clone{len(list(tmp_path.glob('clone*')))}"
    git(tmp_path, "clone", "-q", str(bare), str(clone))
    (clone / path).parent.mkdir(parents=True, exist_ok=True)
    (clone / path).write_text(text)
    git(clone, "add", "-A")
    git(clone, "-c", "user.name=Contributor", "-c", "user.email=c@example.org", "commit", "-qm", "public fix")
    git(clone, "push", "-q", "origin", "main")


def test_push_records_and_status_passes_on_identical_pair(proj, public):
    private, pub = publish_now(proj, public)
    last = publish.read_last(proj)
    assert last["private"] == private and last["public"] == pub
    # LAST_PUBLISHED is committed; only the new review report is left for the user to commit
    assert git(proj, "status", "--porcelain").stdout.strip() == "?? publish/reports/"
    files = set(git(public, "ls-tree", "-r", "--name-only", "main").stdout.split())
    assert "tasks/t01-fit/context.md" in files and publish.SITE_WORKFLOW in files
    assert not [f for f in files if f.startswith(("publish/", "lit_cache/", "contracts/"))]
    assert publish.status(proj, str(public)) == ([], [])


def test_push_refuses_unreviewed_export(proj, public):
    n, _, ex = publish.check(proj)
    with open(proj / "tasks/t01-fit/context.md", "a") as f:
        f.write("changed after the review\n")
    commit(proj)
    with pytest.raises(publish.PublishError, match="export id"):
        publish.push(proj, ex.export_id, str(public))


def test_push_refuses_failing_checks(proj, public):
    with open(proj / "tasks/t01-fit/context.md", "a") as f:
        f.write("see /scratch/grp/x\n")
    commit(proj)
    n, _, ex = publish.check(proj)
    assert n > 0
    with pytest.raises(publish.PublishError, match="check"):
        publish.push(proj, ex.export_id, str(public))


def test_status_reports_pending_and_drift(proj, public, tmp_path):
    publish_now(proj, public)
    with open(proj / "tasks/t01-fit/context.md", "a") as f:
        f.write("new private work\n")
    commit(proj)
    drift, pending = publish.status(proj, str(public))
    assert drift == [] and pending == ["differs: tasks/t01-fit/context.md"]
    public_edit(tmp_path, public, "tasks/t01-fit/log.md", "fixed a typo\n")
    drift, _ = publish.status(proj, str(public))
    assert drift and "pull-public" in drift[0]
    n, _, ex = publish.check(proj)
    with pytest.raises(publish.PublishError, match="changes the private repo lacks"):
        publish.push(proj, ex.export_id, str(public))


def test_pull_public_carries_edit_into_a_branch(proj, public, tmp_path):
    publish_now(proj, public)
    private_only = (proj / "contracts/main.md").read_text()
    main_before = git(proj, "rev-parse", "main").stdout.strip()
    public_edit(tmp_path, public, "tasks/t01-fit/log.md", "fixed a typo\n")
    branch, names = publish.pull_public(proj, str(public))
    assert names == ["tasks/t01-fit/log.md"]
    # main and the working tree are untouched; the branch changes only the public file
    assert git(proj, "rev-parse", "main").stdout.strip() == main_before
    changed = git(proj, "diff", "--name-only", f"main...{branch}").stdout.split()
    assert changed == ["tasks/t01-fit/log.md"]
    assert git(proj, "show", f"{branch}:contracts/main.md").stdout == private_only
    # after the user merges it, the next publish goes through
    git(proj, *USER, "merge", "-q", "--no-edit", branch)
    assert publish.status(proj, str(public))[0] == []
    with open(proj / "tasks/t01-fit/context.md", "a") as f:
        f.write("more work\n")
    commit(proj)
    publish_now(proj, public)
    assert publish.status(proj, str(public)) == ([], [])


def test_pull_public_refuses_paths_outside_the_manifest(proj, public, tmp_path):
    publish_now(proj, public)
    public_edit(tmp_path, public, "contracts/main.md", "rewritten on the public side\n")
    with pytest.raises(publish.PublishError, match="does not export"):
        publish.pull_public(proj, str(public))


# ------------------------------------------------------------------ human-verified guard hook

GUARD = TEMPLATE.parent / "plugins/open-science-project/scripts/human_verified_guard.sh"


@pytest.fixture
def tproj(tmp_path):
    """The two files that mark a template project for the guard."""
    (tmp_path / "config").mkdir()
    (tmp_path / "AGENTS.md").write_text("x\n")
    (tmp_path / "config" / "framework.yaml").write_text("x: 1\n")
    return tmp_path


def guard(tool, tool_input, root=None):
    ti = dict(tool_input)
    if root is not None and "file_path" in ti:
        ti["file_path"] = str(root / ti["file_path"])
    payload = json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": ti})
    return subprocess.run(["bash", str(GUARD)], input=payload, capture_output=True, text=True)


REFUSED = [
    ("Write", {"file_path": "tasks/t/context.md", "content": "---\nverification: human-verified\n---\n"}),
    ("Edit", {"file_path": "x.md", "old_string": "verification: verified", "new_string": "verification: human-verified"}),
    ("MultiEdit", {"file_path": "x.md", "edits": [{"old_string": "a", "new_string": "b"},
                                                  {"old_string": "c", "new_string": "verification:  'human-verified'"}]}),
]


@pytest.mark.parametrize("tool,tool_input", REFUSED)
def test_guard_refuses_an_agent_setting_human_verified(tproj, tool, tool_input):
    r = guard(tool, tool_input, tproj)
    assert r.returncode == 2 and "only the user" in r.stderr


@pytest.mark.parametrize("tool,tool_input", REFUSED)
def test_guard_is_silent_outside_a_template_project(tmp_path, tool, tool_input):
    assert guard(tool, tool_input, tmp_path).returncode == 0


@pytest.mark.parametrize("tool,tool_input", [
    ("Write", {"file_path": "x.md", "content": "---\nverification: verified\nevidence: p.yaml\n---\n"}),
    ("Edit", {"file_path": "x.md", "old_string": "verification: human-verified", "new_string": "verification: verified"}),
    # Bash is not checked: a search for the string must never be refused (the publish check
    # catches an agent commit that sets it, test_human_verified_by_agent_commit_is_refused)
    ("Bash", {"command": "grep -rn 'verification: human-verified' tasks/"}),
])
def test_guard_allows_everything_else(tproj, tool, tool_input):
    assert guard(tool, tool_input, tproj).returncode == 0


# ------------------------------------------------------------------ a repo without the template

PLAIN_MANIFEST = """policy:
  default_privacy: public
  collaborators_agreed: true
include:
  - path: README.md
  - path: src
  - path: notes
never: []
"""


def plain_repo(root):
    """Publishing on its own (component 1): any git repo with a publish manifest."""
    (root / "src").mkdir(parents=True)
    (root / "notes").mkdir()
    (root / "publish").mkdir()
    (root / "README.md").write_text("# plain\n")
    (root / "src" / "a.py").write_text("print(1)\n")
    (root / "notes" / "notes.md").write_text("# Notes\n\nNo front matter here.\n")
    (root / "private.txt").write_text("not in the manifest\n")
    (root / "publish" / "manifest.yaml").write_text(PLAIN_MANIFEST)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "-c", "user.name=T", "-c", "user.email=t@example.org", "commit", "-qm", "init")
    return root


def test_publish_check_works_without_the_template(tmp_path):
    root = plain_repo(tmp_path / "plain")
    n, report, ex = publish.check(root)
    assert n == 0, report.read_text()
    assert sorted(ex.files) == ["README.md", "notes/notes.md", "src/a.py"]
    assert "map and `status:` header checks were skipped" in report.read_text()


def test_template_checks_apply_once_the_repo_is_a_template_project(tmp_path):
    # control: the same repo with the two marker files gets the map and status checks
    root = plain_repo(tmp_path / "plain")
    (root / "config").mkdir()
    (root / "AGENTS.md").write_text("x\n")
    (root / "config" / "framework.yaml").write_text("x: 1\n")
    git(root, "add", "-A")
    git(root, "-c", "user.name=T", "-c", "user.email=t@example.org", "commit", "-qm", "mark")
    n, report, _ = publish.check(root)
    text = report.read_text()
    assert n > 0 and "[map]" in text and "[status] notes/notes.md" in text
