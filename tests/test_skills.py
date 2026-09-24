"""The framework's skills, one set per plugin: present, well-formed, and every command and
script they name exists (S2; split into plugins 2026-09-24)."""

import re
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

from conftest import REPO, run_opsci

PLUGINS = {
    "open-science": ("onboard",),
    "open-science-publish": ("publish", "zenodo-release"),
    "open-science-project": ("new-project", "new-task", "context-files", "migrate-project",
                             "update-from-template"),
    "open-science-context": ("context-management", "continue-context", "advise-with-context"),
}
PLUGIN_OF = {s: p for p, ss in PLUGINS.items() for s in ss}
SKILLS = tuple(PLUGIN_OF)
MAX_LINES = 150  # "keep each SKILL.md short"

SCRIPT_RE = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/scripts/([\w.-]+)")
OPSCI_RE = re.compile(r"\bopsci ([a-z][\w-]*)(?: ([a-z][\w-]*))?")
FULL_REF_RE = re.compile(r"(?<![\w-])(open-science(?:-[a-z]+)?):([a-z][\w-]*)")
# A skill named in backticks without the plugin prefix: resolves to a same-named personal
# skill instead (S0 result 2), so skills must use full names.
BARE_REF_RE = re.compile(r"(?<![:\w-])`(" + "|".join(SKILLS) + r")`")


@lru_cache(maxsize=None)
def opsci_ok(group, cmd):
    args = [group] + ([cmd] if cmd else []) + ["--help"]
    return run_opsci(*args).returncode == 0


def problems(skill_md: Path, name: str, plugin: Path | None = None) -> list[str]:
    plugin = plugin or skill_md.parent.parent.parent
    text = skill_md.read_text()
    out = []
    if not text.startswith("---\n"):
        return ["no front matter"]
    fm = yaml.safe_load(text.split("---\n")[1])
    if fm.get("name") != name:
        out.append(f"front matter name {fm.get('name')!r} != directory {name!r}")
    if len(fm.get("description", "")) < 40:
        out.append("description missing or too short to trigger on")
    if len(text.splitlines()) > MAX_LINES:
        out.append(f"{len(text.splitlines())} lines > {MAX_LINES}")
    for s in SCRIPT_RE.findall(text):
        if not (plugin / "scripts" / s).is_file():
            out.append(f"names missing plugin script {s}")
    for g, c in set(OPSCI_RE.findall(text)):
        if not opsci_ok(g, c or None):
            out.append(f"names unknown command 'opsci {g} {c}'".rstrip("' ") + "'")
    for plug, ref in FULL_REF_RE.findall(text):
        if PLUGIN_OF.get(ref) != plug:
            out.append(f"refers to unknown skill {plug}:{ref}")
    for ref in BARE_REF_RE.findall(text):
        out.append(f"bare skill name `{ref}`: use {PLUGIN_OF[ref]}:{ref}")
    return out


def skill_md(name):
    return REPO / "plugins" / PLUGIN_OF[name] / "skills" / name / "SKILL.md"


@pytest.mark.parametrize("name", SKILLS)
def test_skill_well_formed(name):
    assert skill_md(name).is_file()
    assert problems(skill_md(name), name) == []


@pytest.mark.parametrize("plugin", PLUGINS)
def test_no_extra_skill_dirs(plugin):
    dirs = {p.name for p in (REPO / "plugins" / plugin / "skills").iterdir() if p.is_dir()}
    assert dirs == set(PLUGINS[plugin])


# ---- control fixtures: the checker must refuse each of these ------------------------------

GOOD = """---
name: demo
description: A description long enough to be a trigger for the skill tool.
---
Run `opsci map build` and `bash ${CLAUDE_PLUGIN_ROOT}/scripts/jump.sh active x`.
Then use open-science-context:continue-context.
"""


@pytest.mark.parametrize("mutate,expect", [
    (lambda s: s, None),
    (lambda s: s.replace("name: demo", "name: other"), "front matter name"),
    (lambda s: s.replace("opsci map build", "opsci map nosuch"), "unknown command"),
    (lambda s: s.replace("opsci map build", "opsci nosuch"), "unknown command"),
    (lambda s: s.replace("jump.sh", "gone.sh"), "missing plugin script"),
    (lambda s: s.replace("open-science-context:continue-context", "open-science:nosuch"), "unknown skill"),
    (lambda s: s.replace("open-science-context:continue-context", "open-science-project:continue-context"),
     "unknown skill"),                                   # a real skill under the wrong plugin
    (lambda s: s.replace("open-science-context:continue-context", "`continue-context`"), "bare skill name"),
    (lambda s: s + "x\n" * MAX_LINES, "lines >"),
])
def test_checker_controls(tmp_path, mutate, expect):
    md = tmp_path / "SKILL.md"
    md.write_text(mutate(GOOD))
    got = problems(md, "demo", REPO / "plugins" / "open-science-context")
    if expect is None:
        assert got == []
    else:
        assert any(expect in p for p in got), got


# ---- user guide checker (opsci guide check) -----------------------------------------------

GUIDE_OK = """# User guide
Where things stand: `context.md` and `tasks/<id>/context.md`. Run `opsci map build`.
Take over a pane with `open-science-context:continue-context`. Tests: `tests/run_all`.
Your config: `~/.tmux.conf`. A `key:value` pair is not a skill.
"""


@pytest.mark.parametrize("mutate,expect", [
    (lambda s: s, None),
    (lambda s: s + "word " * 600, "over the limit"),
    (lambda s: s.replace("tests/run_all", "tests/run_none"), "path that does not exist"),
    (lambda s: s.replace("tasks/<id>/context.md", "jobs/<id>/context.md"), "path that does not exist"),
    (lambda s: s.replace("opsci map build", "opsci map nosuch"), "unknown command"),
    (lambda s: s.replace("open-science-context:continue-context", "open-science:nosuch"), "unknown skill"),
    (lambda s: s + "Optional: `slurm-resurrect:nosuch`.\n", "unknown skill"),
])
def test_guide_checker_controls(tmp_path, mutate, expect):
    g = tmp_path / "USER_GUIDE.md"
    g.write_text(mutate(GUIDE_OK))
    r = run_opsci("guide", "check", g, "--repo", REPO)
    if expect is None:
        assert r.returncode == 0, r.stderr
    else:
        assert r.returncode == 1 and expect in r.stderr, r.stderr


def test_user_guide_passes_when_present():
    """USER_GUIDE.md is written in S8; once it exists it must pass the checker."""
    if not (REPO / "USER_GUIDE.md").exists():
        pytest.skip("USER_GUIDE.md not written yet (build plan S8)")
    r = run_opsci("guide", "check", "--repo", REPO)
    assert r.returncode == 0, r.stderr


def test_new_project_asks_about_every_project_md_section():
    """new-project asks what the project is about, naming each section PROJECT.md has."""
    skill = skill_md("new-project").read_text().lower()
    heads = re.findall(r"^## (.+)$", (REPO / "template" / "PROJECT.md").read_text(), re.M)
    assert heads and "always also ask what the project is about" in skill
    missing = [h for h in heads if h.lower() not in skill]
    assert missing == [], missing
