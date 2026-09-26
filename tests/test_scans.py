# opsci: planted-leaks (this file plants leaks and secrets as refusal cases)
"""The leak scan and the secrets scan (publish filter row)."""
import os
import struct
import subprocess
import zlib

import pytest

from conftest import REPO
from opsci import leakscan, secretscan
from opsci.leakscan import PLANTED_MARKER

# Fake identifiers, so the tests do not depend on who runs them.
USER, HOST = "jdoe42", "node17.cluster.example-univ.edu"


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setattr(leakscan.getpass, "getuser", lambda: USER)
    monkeypatch.setattr(leakscan.socket, "gethostname", lambda: HOST)
    monkeypatch.setattr(leakscan.socket, "getfqdn", lambda: HOST)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


def leak_names(tmp_path, text, name="notes.md", root=None):
    (tmp_path / name).write_bytes(text if isinstance(text, bytes) else text.encode())
    pats = leakscan.patterns_for(root)
    return sorted({h.pattern for h in leakscan.scan_tree(tmp_path, pats)})


LEAKS = [
    ("output in /scratch/group/run1/out.h5", "absolute-path"),
    ("see /home/someone/notes", "absolute-path"),
    ("mail me at real.person@univ.edu", "email"),
    ("x@y.io", "email"),
    ("ssh -T someone@github.com", "email"),  # only the git login is allowed
    ("login node 10.12.0.5", "ipv4"),
    ("host=10.12.0.5.", "ipv4"),
    ("jobid=4412345", "slurm-job-id"),
    ("array task 20888489_3 failed", "slurm-array-id"),
    ("see slurm-4412345.out", "slurm-out-file"),
    (f"ran as {USER} yesterday", "site-identifier (user name)"),
    (f"on {HOST}", "site-identifier (host name)"),
    ("the cluster.example-univ.edu file system", "site-identifier (host name)"),
]


@pytest.mark.parametrize("text,pattern", LEAKS)
def test_leak_refused(tmp_path, site, text, pattern):
    assert pattern in leak_names(tmp_path, text)


CLEAN = [
    "a relative path data/run1/out.h5 and ~/config",
    "a URL https://example.org/a/b and #!/usr/bin/env python",
    "reserved addresses someone@example.org, x@lab.invalid, loopback 127.0.0.1, doc 192.0.2.7",
    "GitHub's SSH login: ssh -T git@github.com",
    "version 1.2.3 and a date 2026-09-23",
    # package versions in lock files
    "conda-forge/linux-64/alsa-lib-1.2.16.1-h7cc23a3_1.conda",
    "- astropy-iers-data >=0.2026.6.22.1.23.34",
    "- pyerfa >=2.0.1.3 and numpy==1.2.3.4",
    f"a longer word {USER}x does not match the user name",
]


@pytest.mark.parametrize("text", CLEAN)
def test_clean_text_passes(tmp_path, site, text):
    assert leak_names(tmp_path, text) == []


def test_file_name_is_scanned(tmp_path, site):
    (tmp_path / f"{USER}-notes.md").write_text("clean\n")
    hits = leakscan.scan_tree(tmp_path, leakscan.patterns_for(None))
    assert [h.where for h in hits] == ["filename"]


def test_site_config_values_are_scanned(tmp_path, site):
    proj = tmp_path / "proj"
    (proj / "config").mkdir(parents=True)
    (proj / "config/site.local.yaml").write_text(
        "scratch: /scratch/bigfs/grp\nbatch:\n  account: phy99999\n  partition: wholenode\n"
        "identifiers: [Rivendell]\n")
    tree = tmp_path / "tree"
    tree.mkdir()
    for text in ("submitted with phy99999", "submitted with Rivendell", "#SBATCH -p wholenode",
                 "sbatch --partition=wholenode", "-pwholenode", "partition: wholenode", 'partition "wholenode"'):
        (tree / "a.md").write_text(text + "\n")
        names = {h.pattern for h in leakscan.scan_tree(tree, leakscan.patterns_for(proj))}
        assert any("site.local.yaml" in n for n in names), text
    # a partition named by a plain word is ordinary English outside a partition context
    (tree / "a.md").write_text("a wholenode layout; the partition itself is not named\n")
    assert leakscan.scan_tree(tree, leakscan.patterns_for(proj)) == []
    # a partition name that is not a plain word matches anywhere
    (proj / "config/site.local.yaml").write_text("batch:\n  partition: gpu-a100\n")
    (tree / "a.md").write_text("ran on gpu-a100\n")
    assert leakscan.scan_tree(tree, leakscan.patterns_for(proj)) != []
    # placeholders in an unfilled config are not patterns
    (proj / "config/site.local.yaml").write_text("scratch: <path>\nbatch:\n  account: <acct>\n")
    (tree / "a.md").write_text("clean text\n")
    assert leakscan.scan_tree(tree, leakscan.patterns_for(proj)) == []


def test_private_policy_patterns(tmp_path, site):
    proj = tmp_path / "proj"
    (proj / "publish").mkdir(parents=True)
    policy = proj / "publish/PRIVATE_POLICY.md"
    policy.write_text("# Private policy\n\n## Patterns (one per line)\n\n```\n# comment\n"
                      "Project\\s+Nightjar\n```\n\n## Other\n\n```\nnot-a-pattern\n```\n")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.md").write_text("results of Project  Nightjar\nnot-a-pattern\n")
    hits = leakscan.scan_tree(tree, leakscan.patterns_for(proj))
    assert [h.match for h in hits] == ["Project  Nightjar"]
    policy.write_text("## Patterns\n```\n(unclosed\n```\n")
    with pytest.raises(leakscan.LeakScanError):
        leakscan.patterns_for(proj)


def _png_with_text(key: bytes, text: bytes, compressed: bool) -> bytes:
    def chunk(t, body):
        return struct.pack(">I", len(body)) + t + body + struct.pack(">I", zlib.crc32(t + body))
    body = key + b"\0" + (b"\0" + zlib.compress(text) if compressed else text)
    return (leakscan.PNG_MAGIC + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
            + chunk(b"zTXt" if compressed else b"tEXt", body) + chunk(b"IEND", b""))


@pytest.mark.parametrize("compressed", [False, True])
def test_png_metadata_is_scanned(tmp_path, site, compressed):
    (tmp_path / "fig.png").write_bytes(_png_with_text(b"Source", b"/scratch/grp/fig.py", compressed))
    hits = leakscan.scan_tree(tmp_path, leakscan.patterns_for(None))
    assert "image-metadata" in {h.where for h in hits}
    (tmp_path / "fig.png").write_bytes(_png_with_text(b"Software", b"matplotlib", compressed))
    assert leakscan.scan_tree(tmp_path, leakscan.patterns_for(None)) == []


def test_png_pixels_are_not_scanned(tmp_path, site):
    # compressed pixel data can hold a path-like byte run by chance
    png = _png_with_text(b"Software", b"matplotlib", False)
    idat = b"IDAT" + b"\xa6\xbb/v/m\xe1I{"
    png = png[:-12] + struct.pack(">I", len(idat) - 4) + idat + struct.pack(">I", zlib.crc32(idat)) + png[-12:]
    (tmp_path / "fig.png").write_bytes(png)
    assert leakscan.scan_tree(tmp_path, leakscan.patterns_for(None)) == []


def test_binary_data_skips_chance_patterns(tmp_path, site):
    # an email-like and an IP-like byte run inside binary data is chance, not a leak ...
    (tmp_path / "grid.bin").write_bytes(b"\xff\xfeR@IW7.Rq\x00" + b"\x00\x0110.1.2.3\x00\xff")
    assert leakscan.scan_tree(tmp_path, leakscan.patterns_for(None)) == []
    # ... but a path or a user name inside binary data is refused
    (tmp_path / "grid.bin").write_bytes(b"\xff\xfe/scratch/grp/x.h5\x00" + USER.encode() + b"\x00")
    names = {h.pattern for h in leakscan.scan_tree(tmp_path, leakscan.patterns_for(None))}
    assert names == {"absolute-path", "site-identifier (user name)"}


def _pdf(info: bytes = b"(fig)", objstm: bytes = b"<< >>") -> bytes:
    """A one-page PDF with the syntax that looks like a path: glyph-name runs in /CharSet and
    /Differences, and a compressed stream whose bytes happen to contain '/ab/cd'."""
    noise = zlib.compress(b"\x00" * 64)[:2] + b"\x93/t/5q\xa9/l\xce" + b"\x00" * 8
    def stream(head: bytes, body: bytes) -> bytes:
        return head + b" /Length " + str(len(body)).encode() + b" >>\nstream\n" + body + b"\nendstream"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R >>",
            b"<< /FontName /CMR10 /CharSet (/M/N/h/i/period/r/slash/t) >>",
            b"<< /Differences[0/Gamma/Delta/Theta] >>",
            stream(b"<<", noise), stream(b"<< /Type /ObjStm /Filter /FlateDecode", zlib.compress(objstm)),
            b"<< /Title " + info + b" >>"]
    return (b"%PDF-1.5\n" + b"".join(b"%d 0 obj\n" % i + o + b"\nendobj\n" for i, o in enumerate(objs, 1))
            + b"%%EOF\n")


def test_pdf_syntax_is_not_a_leak(tmp_path, site):
    from opsci import pdf
    assert pdf.page_count(_pdf()) == 1
    (tmp_path / "fig.pdf").write_bytes(_pdf())
    assert leakscan.scan_tree(tmp_path, leakscan.patterns_for(None)) == []


@pytest.mark.parametrize("where", ["info", "objstm"])
def test_pdf_strings_are_scanned(tmp_path, site, where):
    leak = b"(/scratch/grp/fig.py by real.person@univ.edu)"
    data = _pdf(info=leak) if where == "info" else _pdf(objstm=b"<< /Title " + leak + b" >>")
    (tmp_path / "fig.pdf").write_bytes(data)
    names = {h.pattern for h in leakscan.scan_tree(tmp_path, leakscan.patterns_for(None))}
    assert names == {"absolute-path", "email"}


def test_planted_marker_only_in_tests(tmp_path, site):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/a.py").write_text(f"# {PLANTED_MARKER}\nx = '/scratch/a/b'\n")
    (tmp_path / "b.md").write_text(f"{PLANTED_MARKER}\n/scratch/a/b\n")
    pats = leakscan.patterns_for(None)
    assert [h.path for h in leakscan.scan_tree(tmp_path, pats, honour_planted_marker=True)] == ["b.md"]
    # a project export never honours the marker
    assert len(leakscan.scan_tree(tmp_path, pats)) == 2


# Built at run time so that no literal token sits in the repo.
SECRETS = [
    ("-----BEGIN RSA " + "PRIVATE KEY-----\nMIIE\n", "private-key"),
    ("SLACK=" + "xox" + "b-1234567890-abcdefghijkl", "slack-token"),
    ("https://hooks." + "slack.com/services/T0000/B0000/abcdefghijklmnopqrstuvwx", "slack-webhook"),
    ("gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8", "github-token"),
    ("key AK" + "IAIOSFODNN7EXAMPLQ", "aws-access-key"),
    ("AI" + "za" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q", "google-api-key"),
    ("sk-" + "ant-" + "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7", "llm-api-key"),
    ("ZENODO_TOKEN = 'q8Zr" + "T4mW9xLk2PvB7nYc3D'", "assigned-secret"),
    ("password: hunter2" + "hunter2hunter2", "assigned-secret"),
    ("git clone https://bob:s3cr" + "etpw@git.example.org/r.git", "url-credentials"),
]


@pytest.mark.parametrize("text,pattern", SECRETS)
def test_secret_refused(tmp_path, text, pattern):
    (tmp_path / "f.txt").write_text(text + "\n")
    hits, ran = secretscan.scan(tmp_path)
    assert pattern in {h.pattern for h in hits}
    assert "built-in patterns" in ran
    # the report never repeats the whole secret
    secret = text.split()[-1].split("=")[-1]
    assert all(secret not in h.render() for h in hits if len(secret) > 12)


PLACEHOLDERS = [
    "token: <your token>",
    "SLACK_TOKEN=${SLACK_TOKEN}",
    "password = changeme",
    "api_key: xxxxxxxxxxxxxxxxxxxx",
    "the token is read from a private file (mode 600)",
]


@pytest.mark.parametrize("text", PLACEHOLDERS)
def test_placeholder_is_not_a_secret(tmp_path, text):
    (tmp_path / "f.txt").write_text(text + "\n")
    assert secretscan.scan(tmp_path)[0] == []


def _framework_files():
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", "-co", "--exclude-standard"],
                         capture_output=True, text=True, check=True).stdout.split()
    return [f for f in out if (REPO / f).is_file() and not f.endswith(".lock")]


def test_framework_repo_passes_its_own_scans():
    """The leak and secrets scans over the framework's own tracked files (plan, Generality)."""
    files = _framework_files()
    patterns = leakscan.patterns_for(None)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        # On GitHub's machines the user name is "runner", an ordinary word, and the user and
        # host name belong to the CI machine, not to anyone who could leak them into this repo.
        patterns = [p for p in patterns if not p.name.startswith("site-identifier")]
    leaks = leakscan.scan_tree(REPO, patterns, files, honour_planted_marker=True)
    assert leaks == [], leakscan.format_hits(leaks)
    secrets, _ = secretscan.scan(REPO, files, honour_planted_marker=True)
    assert [h for h in secrets if not h.pattern.startswith("gitleaks")] == []


def test_escaped_markup_is_not_an_absolute_path(tmp_path):
    # a built page escapes `tasks/<id>/results/README.md`; the `;` must not start a path
    assert leak_names(tmp_path, "<code>tasks/&lt;id&gt;/results/README.md</code>", "index.html") == []
    assert leak_names(tmp_path, '{"text": "tasks/&lt;id&gt;/results/README.md"}', "search_index.json") == []
    assert leak_names(tmp_path, "<p>see &#47;scratch/grp/run1</p>", "page.html") == ["absolute-path"]


def test_github_runner_account_is_not_a_site_identifier(tmp_path, site, monkeypatch):
    monkeypatch.setattr(leakscan.getpass, "getuser", lambda: "runner")
    assert leak_names(tmp_path, "the chunk runner") == ["site-identifier (user name)"]
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert leak_names(tmp_path, "the chunk runner") == []
