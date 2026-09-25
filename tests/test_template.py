# opsci: planted-leaks (this file plants fake absolute paths on purpose)
"""Template tests (report, Tests table, row "template").

A fresh copy has every required file; no placeholder or absolute path survives
instantiation; .gitignore excludes data/. Each check has a case it must refuse.
"""
import datetime as dt
import re
import shutil

import pytest

from conftest import TEMPLATE, git, run_opsci
from opsci import template as T

DATE = dt.date(2026, 9, 23)


def make(tmp_path, **kw):
    args = dict(name="demo-proj", title="Demo project", author="A. Person", date=DATE,
                template=TEMPLATE, framework_repo="https://example.org/open-science")
    args.update(kw)
    return T.instantiate(tmp_path / "proj", **args)


def test_fresh_copy_passes_its_check(tmp_path):
    proj = make(tmp_path)
    assert T.verify_instance(proj) == []
    for rel in T.REQUIRED:
        assert (proj / rel).exists(), rel
    assert (proj / "log" / "2026-09.md").exists()
    assert (proj / "map" / "graph.md").exists() and (proj / "map" / "dead_ends.md").exists()


def test_cli_instantiate_and_check(tmp_path):
    dest = tmp_path / "p"
    r = run_opsci("template", "instantiate", dest, "--name", "p1", "--title", "T", "--author", "A",
                  "--template", TEMPLATE, "--framework-repo", "https://example.org/fw")
    assert r.returncode == 0, r.stderr
    assert run_opsci("template", "check", dest).returncode == 0


def test_refuses_missing_required_file(tmp_path):
    proj = make(tmp_path)
    (proj / "contracts" / "subagent.md").unlink()
    assert "missing: contracts/subagent.md" in T.verify_instance(proj)
    assert run_opsci("template", "check", proj).returncode == 1


def test_project_description_is_created_unfilled(tmp_path):
    proj = make(tmp_path)
    text = (proj / "PROJECT.md").read_text()
    assert text.startswith("# Demo project: what this project is about\n")
    heads = re.findall(r"^## (.+)$", text, re.M)
    assert heads == ["The question", "Why it matters", "Approach", "What counts as success",
                     "Scope", "Key sources"]
    assert text.count("\nTODO:\n") == len(heads)  # every section waits for the user
    for f in ("AGENTS.md", "README.md"):
        assert "PROJECT.md" in (proj / f).read_text(), f


def test_refuses_missing_project_description(tmp_path):
    proj = make(tmp_path)
    (proj / "PROJECT.md").unlink()
    assert "missing: PROJECT.md" in T.verify_instance(proj)


def test_all_placeholders_filled(tmp_path):
    proj = make(tmp_path)
    text = (proj / "LICENSE").read_text() + (proj / "config" / "framework.yaml").read_text()
    assert "2026 A. Person" in text and "https://example.org/open-science" in text


def test_refuses_leftover_placeholder(tmp_path):
    proj = make(tmp_path)
    (proj / "README.md").write_text("# {{PROJECT_TITLE}}\n")
    assert any("placeholder {{PROJECT_TITLE}}" in p for p in T.verify_instance(proj))


CONTEXT_WORDS = ("open-science-context", "session jump", "jumps")


def context_hits(proj):
    # config/framework.yaml records the choice, so it names the component either way
    return sorted({f"{p.relative_to(proj)}" for p, text in T.text_files(proj)
                   if any(w in text for w in CONTEXT_WORDS)} - {"config/framework.yaml"})


def test_context_management_component_is_kept_by_default(tmp_path):
    proj = make(tmp_path)
    assert "open-science-context:context-management" in (proj / "CLAUDE.md").read_text()
    assert "context_management: true" in (proj / "config" / "framework.yaml").read_text()
    assert not any("opsci:" in t and "-->" in t for _, t in T.text_files(proj))


def test_without_context_management_no_trace_of_it(tmp_path):
    proj = make(tmp_path, context_management=False)
    assert T.verify_instance(proj) == []
    assert context_hits(proj) == []
    assert "context_management: false" in (proj / "config" / "framework.yaml").read_text()
    # the replacement text is there instead
    assert "read AGENTS.md, the project context.md" in (proj / ".claude/agents/med-effort.md").read_text()
    # control: the default project does mention it, so the check can fail
    assert context_hits(make(tmp_path / "b")) != []


def test_cli_no_context_management(tmp_path):
    r = run_opsci("template", "instantiate", tmp_path / "p", "--name", "p", "--title", "P",
                  "--author", "A", "--template", TEMPLATE, "--no-context-management")
    assert r.returncode == 0, r.stderr
    assert "context_management: false" in (tmp_path / "p" / "config" / "framework.yaml").read_text()


def test_refuses_leftover_component_marker(tmp_path):
    proj = make(tmp_path)
    (proj / "README.md").write_text("<!-- opsci:context -->\nx\n")
    assert any("component marker" in p for p in T.verify_instance(proj))


def test_refuses_unknown_placeholder_in_template(tmp_path):
    tpl = tmp_path / "tpl"
    shutil.copytree(TEMPLATE, tpl)
    (tpl / "README.md").write_text("{{NOT_A_PLACEHOLDER}}\n")
    with pytest.raises(T.TemplateError, match="unknown placeholders"):
        T.instantiate(tmp_path / "proj", "demo", "Demo", "A", template=tpl, date=DATE,
                      framework_repo="https://example.org/x")
    assert not (tmp_path / "proj").exists()  # nothing half-made is left behind


@pytest.mark.parametrize("text", [
    "see /home/someone/project/file.txt",
    "scratch at /scratch/abc123/run",
    "cd /Users/me/work && ls",
])
def test_refuses_absolute_path(tmp_path, text):
    proj = make(tmp_path)
    (proj / "context.md").write_text(text + "\n")
    assert any("absolute path" in p for p in T.verify_instance(proj))


@pytest.mark.parametrize("text", [
    "https://example.org/a/b and http://x.io/c/d",
    "relative src/common/io.py and data/t07/out",
    "a per-user file ~/.config/opsci/notify.env",
    "discard output to /dev/null",
    "#!/usr/bin/env bash",
])
def test_accepts_non_absolute_paths(tmp_path, text):
    proj = make(tmp_path)
    (proj / "context.md").write_text(text + "\n")
    assert T.verify_instance(proj) == []


def test_refuses_local_framework_path(tmp_path):
    with pytest.raises(T.TemplateError, match="absolute path"):
        make(tmp_path, framework_repo="/some/local/checkout/open-science")


def test_gitignore_excludes_data(tmp_path):
    proj = make(tmp_path)
    git(proj, "init", "-q")
    (proj / "data" / "t01").mkdir()
    (proj / "data" / "t01" / "big.h5").write_text("x")
    (proj / "config" / "site.local.yaml").write_text("scratch: x\n")
    git(proj, "add", "-A")
    tracked = git(proj, "ls-files").stdout.split()
    assert "data/MANIFEST.yaml" in tracked
    assert "data/t01/big.h5" not in tracked
    assert "config/site.local.yaml" not in tracked


def test_refuses_gitignore_without_data_rule(tmp_path):
    proj = make(tmp_path)
    gi = proj / ".gitignore"
    gi.write_text("\n".join(l for l in gi.read_text().splitlines() if not l.startswith("/data")) + "\n")
    probs = T.verify_instance(proj)
    assert any("data/some-task/output.h5' should be ignored" in p for p in probs)


@pytest.mark.parametrize("kw,msg", [
    ({"name": "Bad Name"}, "project name"),
    ({"title": 'has "quotes"'}, "title"),
    ({"author": ""}, "author"),
])
def test_refuses_bad_arguments(tmp_path, kw, msg):
    with pytest.raises(T.TemplateError, match=msg):
        make(tmp_path, **kw)


def test_refuses_nonempty_destination(tmp_path):
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "keep.txt").write_text("mine")
    with pytest.raises(T.TemplateError, match="not empty"):
        make(tmp_path)
    assert (tmp_path / "proj" / "keep.txt").read_text() == "mine"


def test_agent_definitions_parse(tmp_path):
    import yaml
    proj = make(tmp_path)
    for f in sorted((proj / ".claude" / "agents").glob("*.md")):
        head = yaml.safe_load(f.read_text().split("---")[1])
        assert head["name"] == f.stem and head["description"] and head["effort"] in ("low", "medium", "high")


def test_claude_md_imports_agents_md():
    # S0 (docs/design/verification.md) confirmed that @AGENTS.md loads AGENTS.md.
    assert (TEMPLATE / "CLAUDE.md").read_text().splitlines()[0].strip() == "@AGENTS.md"


def test_no_zenodo_json():
    # S0: a .zenodo.json makes Zenodo ignore CITATION.cff.
    assert not (TEMPLATE / ".zenodo.json").exists()


# ---- layout 2: brainstorm/, docs/, private-docs/ ------------------------------------------

def test_new_directories_are_created_filled(tmp_path):
    proj = make(tmp_path)
    for rel in ("docs/README.md", "private-docs/README.md", "brainstorm/README.md",
                "brainstorm/context.md", "brainstorm/tasks/README.md",
                "brainstorm/map/README.md", "brainstorm/log/README.md"):
        assert rel in T.REQUIRED, rel
    ctx = (proj / "brainstorm" / "context.md").read_text()
    assert ctx.startswith("# Brainstorm context: Demo project\n") and "2026-09-23" in ctx
    # the brainstorm sub-root gets its own generated map
    assert (proj / "brainstorm" / "map" / "graph.md").exists()
    assert (proj / "brainstorm" / "map" / "dead_ends.md").exists()


@pytest.mark.parametrize("rel", ["brainstorm/context.md", "docs/README.md", "private-docs/README.md"])
def test_refuses_missing_new_directory_file(tmp_path, rel):
    proj = make(tmp_path)
    (proj / rel).unlink()
    assert f"missing: {rel}" in T.verify_instance(proj)


def test_manifest_publishes_docs_but_not_brainstorm_or_private_docs(tmp_path):
    import yaml
    proj = make(tmp_path)
    man = yaml.safe_load((proj / "publish" / "manifest.yaml").read_text())
    include = [e["path"] for e in man["include"]]
    assert "docs" in include
    assert "brainstorm" not in include and "private-docs" not in include
    assert "private-docs" in man["never"]
    # brainstorm is an opt-in: present only as a commented line
    assert "# - path: brainstorm" in (proj / "publish" / "manifest.yaml").read_text()


def test_readme_does_not_link_to_unpublished_directories():
    text = (TEMPLATE / "README.md").read_text()
    for d in ("brainstorm", "private-docs"):
        assert f"`{d}/`" in text                       # listed in the table
        assert not re.search(rf"\]\(\.?/?{d}\b", text)  # but never as a Markdown link
    assert re.search(r"\]\(docs/\)", text)              # control: the published one is linked


def test_template_layout_version_is_the_framework_constant(tmp_path):
    from opsci.layout import LAYOUT_VERSION, read_layout_version
    assert read_layout_version((TEMPLATE / "config" / "framework.yaml").read_text()) == LAYOUT_VERSION
    proj = make(tmp_path)
    assert f"\nlayout_version: {LAYOUT_VERSION}\n" in (proj / "config" / "framework.yaml").read_text()
    assert read_layout_version("copied_on: x\n") == 1   # control: no key means layout 1
