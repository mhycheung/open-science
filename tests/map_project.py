"""A small synthetic project with every kind of node and edge, for the map tests."""
from pathlib import Path

import yaml


def header(**h) -> str:
    return "---\n" + yaml.safe_dump(h, sort_keys=False) + "---\n\n# body\n"


def valid_files() -> dict[str, str]:
    return {
        "tasks/t01-noise-model/context.md": header(
            id="t01-noise-model", title="Noise model", type="task", status="done",
            summary="Gaussian noise model fixed from off-source data.", privacy="public",
            verification="verified", evidence="tasks/t01-noise-model/provenance.yaml"),
        "tasks/t01-noise-model/provenance.yaml": "src_commit: abc1234\ncommand: python fit.py\n",
        "tasks/t02-fit-v1/context.md": header(
            id="t02-fit-v1", title="Fit, first likelihood", type="task", status="superseded",
            depends_on=["t01-noise-model"],
            summary="Likelihood omitted the window normalisation | biased amplitudes."),
        "tasks/t03-fit-v2/context.md": header(
            id="t03-fit-v2", title='Fit with the "corrected" likelihood', type="task", status="active",
            depends_on=["t01-noise-model"], supersedes=["t02-fit-v1"], related=["t04-scan"],
            summary="Refit with the normalised likelihood."),
        "tasks/t03-fit-v2/plan.md": header(
            id="t03-fit-v2", title="Fit with the corrected likelihood", type="task", status="active",
            depends_on=["t01-noise-model"], supersedes=["t02-fit-v1"], related=["t04-scan"],
            summary="Refit with the normalised likelihood.", autonomy="autonomous", hold_at=[]),
        "tasks/t04-scan/context.md": header(
            id="t04-scan", title="Start-time scan", type="task", status="failed",
            related=["t03-fit-v2"], summary="Scan unstable below 10 ms; route dropped."),
        "archive/t00-pilot/context.md": header(
            id="t00-pilot", title="Pilot", type="task", status="abandoned",
            summary="Pilot on simulated data only; not continued."),
        "paper/node.yaml": yaml.safe_dump(dict(
            id="p1-paper", title="Paper draft", type="paper", status="active",
            depends_on=["t03-fit-v2"], summary="Paper on the corrected fit.")),
        "paper/main.tex": "\\documentclass{article}\n",
        "site/results/fit.md": header(
            id="r1-fit-result", title="Fit result page", type="result", status="done",
            depends_on=["t03-fit-v2"], privacy="public", summary="Posterior of the corrected fit."),
        # Not nodes: front matter without an id, and anything under data/ or lit_cache/.
        "README.md": "---\ntitle: site front matter\n---\n# Readme\n",
        "data/t03/notes.md": header(id="x-in-data", title="x", type="task", status="done", summary="x"),
        "lit_cache/paper.md": header(id="x-in-lit", title="x", type="task", status="bogus", summary="x"),
        "publish/manifest.yaml": yaml.safe_dump({"include": [
            {"path": "README.md"},
            {"path": "paper", "type": "paper"},
            {"path": "site/results/*.md", "type": "result"},
        ]}),
    }


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root
