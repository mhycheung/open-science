"""Checks on the documentation site (mkdocs.yml, docs/). Each check has a control case that
must fail, so a check that passes for the wrong reason is caught."""
import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from conftest import REPO
from opsci import guidecheck

DOCS = REPO / "docs"
MKDOCS_YML = REPO / "mkdocs.yml"
HAVE_MKDOCS = (importlib.util.find_spec("mkdocs") is not None
               and importlib.util.find_spec("material") is not None)
needs_mkdocs = pytest.mark.skipif(not HAVE_MKDOCS, reason="mkdocs or mkdocs-material is not installed")

FENCE_RE = re.compile(r"^```([\w-]*)\n(.*?)^```", re.M | re.S)
CHECK_NAME_RE = re.compile(r'Problem\("([a-z-]+)"')


# --------------------------------------------------------------------------- helpers

class _Loader(yaml.SafeLoader):
    """mkdocs.yml uses `!!python/name:` for the mermaid fence; read such tags as None."""


_Loader.add_multi_constructor("tag:yaml.org,2002:python/", lambda loader, suffix, node: None)


def load_config(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)


def nav_pages(nav) -> set[str]:
    out = set()
    for item in nav or []:
        value = next(iter(item.values())) if isinstance(item, dict) else item
        if isinstance(value, list):
            out |= nav_pages(value)
        elif isinstance(value, str):
            out.add(value)
    return out


def site_pages(docs: Path) -> set[str]:
    """Every page under docs/ except the design records and the git-ignored agent working
    files (context/), as paths relative to docs/."""
    return {p.relative_to(docs).as_posix() for p in docs.rglob("*.md")
            if p.relative_to(docs).parts[0] not in ("design", "context")}


def pages_missing_from_nav(root: Path) -> set[str]:
    cfg = load_config(root / "mkdocs.yml")
    return site_pages(root / cfg.get("docs_dir", "docs")) - nav_pages(cfg.get("nav"))


def mkdocs_build(root: Path, out: Path) -> subprocess.CompletedProcess:
    # No --quiet: with it, mkdocs 1.6 does not count warnings and --strict passes anyway.
    return subprocess.run([sys.executable, "-m", "mkdocs", "build", "--strict",
                           "--config-file", str(root / "mkdocs.yml"), "--site-dir", str(out)],
                          capture_output=True, text=True, cwd=root)


def copy_site_sources(dest: Path) -> Path:
    shutil.copy2(MKDOCS_YML, dest / "mkdocs.yml")
    shutil.copytree(DOCS, dest / "docs", ignore=shutil.ignore_patterns("design", "context"))
    return dest


def mermaid_blocks(text: str) -> list[str]:
    return [body for lang, body in FENCE_RE.findall(text) if lang == "mermaid"]


def get_started_problems(text: str) -> list[str]:
    probs = []
    if not mermaid_blocks(text):
        probs.append("no ```mermaid block")
    if "open-science:onboard" not in text:
        probs.append("does not name open-science:onboard")
    return probs


def publish_check_names() -> set[str]:
    return set(CHECK_NAME_RE.findall((REPO / "tools/opsci/publish.py").read_text(encoding="utf-8")))


def checks_missing(text: str, names: set[str]) -> set[str]:
    return {n for n in names if not re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", text)}


def code_text(text: str) -> str:
    """The text of fenced blocks and inline code spans: where the pages name commands."""
    return "\n".join([body for _, body in FENCE_RE.findall(text)] + guidecheck.CODE_RE.findall(text))


def unknown_names(page: Path) -> list[str]:
    """`opsci` commands named in code that do not exist, and `<plugin>:<skill>` names (anywhere)
    for a plugin of this repo that has no such skill."""
    text = page.read_text(encoding="utf-8")
    probs = [f"opsci {g} {c}".rstrip() for g, c in sorted(set(guidecheck.OPSCI_RE.findall(code_text(text))))
             if not guidecheck._opsci_ok(g, c or None)]
    plugins = {p.name: p for d in ("plugins", "extras") for p in (REPO / d).iterdir()
               if (p / ".claude-plugin" / "plugin.json").is_file()}
    probs += [f"{p}:{s}" for p, s in sorted(set(guidecheck.SKILL_RE.findall(text)))
              if p in plugins and not (plugins[p] / "skills" / s / "SKILL.md").is_file()]
    return probs


# --------------------------------------------------------------------------- the build

@needs_mkdocs
def test_strict_build_passes(tmp_path):
    r = mkdocs_build(REPO, tmp_path / "site")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "site" / "index.html").is_file()
    assert not (tmp_path / "site" / "design").exists()  # the design records are excluded


@needs_mkdocs
def test_strict_build_refuses_page_left_out_of_nav(tmp_path):
    root = copy_site_sources(tmp_path)
    cfg = (root / "mkdocs.yml").read_text(encoding="utf-8")
    (root / "mkdocs.yml").write_text(cfg.replace("  - Troubleshooting: faq.md\n", ""), encoding="utf-8")
    assert "faq.md" not in (root / "mkdocs.yml").read_text(encoding="utf-8")
    r = mkdocs_build(root, tmp_path / "site")
    assert r.returncode != 0
    assert "faq.md" in r.stdout + r.stderr


@needs_mkdocs
def test_strict_build_refuses_broken_link(tmp_path):
    root = copy_site_sources(tmp_path)
    page = root / "docs" / "faq.md"
    page.write_text(page.read_text(encoding="utf-8") + "\nSee [nothing](no-such-page.md).\n", encoding="utf-8")
    r = mkdocs_build(root, tmp_path / "site")
    assert r.returncode != 0
    assert "no-such-page.md" in r.stdout + r.stderr


# --------------------------------------------------------------------------- the nav

def test_every_page_is_in_nav():
    assert pages_missing_from_nav(REPO) == set()
    nav = nav_pages(load_config(MKDOCS_YML)["nav"])
    assert all((DOCS / p).is_file() for p in nav), nav


def test_nav_check_finds_page_left_out(tmp_path):
    root = copy_site_sources(tmp_path)
    (root / "docs" / "extra-page.md").write_text("# Not in the nav\n", encoding="utf-8")
    assert pages_missing_from_nav(root) == {"extra-page.md"}


def test_nav_check_ignores_design_records(tmp_path):
    root = copy_site_sources(tmp_path)
    (root / "docs" / "design").mkdir()
    (root / "docs" / "design" / "record.md").write_text("# A design record\n", encoding="utf-8")
    assert pages_missing_from_nav(root) == set()


# --------------------------------------------------------------------------- Get started

def test_get_started_has_chart_and_onboarding():
    assert get_started_problems((DOCS / "index.md").read_text(encoding="utf-8")) == []


def test_get_started_check_refuses_page_without_them():
    text = (DOCS / "index.md").read_text(encoding="utf-8")
    no_chart = FENCE_RE.sub(lambda m: "" if m.group(1) == "mermaid" else m.group(0), text)
    assert get_started_problems(no_chart) == ["no ```mermaid block"]
    no_onboard = text.replace("open-science:onboard", "the onboarding skill")
    assert get_started_problems(no_onboard) == ["does not name open-science:onboard"]


def test_chart_names_every_publish_check():
    names = publish_check_names()
    assert {"leak", "secret", "policy"} <= names  # the regex still finds the checks
    (chart,) = mermaid_blocks((DOCS / "index.md").read_text(encoding="utf-8"))
    assert checks_missing(chart, names) == set()
    table = (DOCS / "publishing.md").read_text(encoding="utf-8")
    assert checks_missing(table, {f"`{n}`" for n in names}) == set()


def test_chart_check_finds_missing_check():
    (chart,) = mermaid_blocks((DOCS / "index.md").read_text(encoding="utf-8"))
    assert checks_missing(chart.replace("human-verified", "human"), publish_check_names()) == {"human-verified"}


# --------------------------------------------------------------------------- names in the pages

def test_pages_name_only_real_commands_and_skills():
    problems = {p.name: unknown_names(p) for p in sorted(DOCS.glob("*.md"))}
    assert {k: v for k, v in problems.items() if v} == {}


def test_name_check_refuses_unknown_command_and_skill(tmp_path):
    page = tmp_path / "page.md"
    page.write_text("Run `opsci map nosuch`, then `opsci map build`, then "
                    "`open-science-project:no-such-skill` and `open-science-project:new-task`.\n"
                    "The opsci command is prose, not a command.\n", encoding="utf-8")
    assert unknown_names(page) == ["opsci map nosuch", "open-science-project:no-such-skill"]
