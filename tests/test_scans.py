# opsci: planted-leaks (this file plants leaks and secrets as refusal cases)
"""The leak scan and the secrets scan (publish filter row)."""
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
    "reserved addresses someone@example.org, x@lab.invalid, loopback 127.0.0.1",
    "GitHub's SSH login: ssh -T git@github.com",
    "version 1.2.3 and a date 2026-09-23",
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
    for word in ("phy99999", "wholenode", "Rivendell"):
        (tree / "a.md").write_text(f"submitted with {word}\n")
        names = {h.pattern for h in leakscan.scan_tree(tree, leakscan.patterns_for(proj))}
        assert any("site.local.yaml" in n for n in names), word
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


def test_binary_data_skips_chance_patterns(tmp_path, site):
    # an email-like and an IP-like byte run inside binary data is chance, not a leak ...
    (tmp_path / "grid.bin").write_bytes(b"\xff\xfeR@IW7.Rq\x00" + b"\x00\x0110.1.2.3\x00\xff")
    assert leakscan.scan_tree(tmp_path, leakscan.patterns_for(None)) == []
    # ... but a path or a user name inside binary data is refused
    (tmp_path / "grid.bin").write_bytes(b"\xff\xfe/scratch/grp/x.h5\x00" + USER.encode() + b"\x00")
    names = {h.pattern for h in leakscan.scan_tree(tmp_path, leakscan.patterns_for(None))}
    assert names == {"absolute-path", "site-identifier (user name)"}


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
    leaks = leakscan.scan_tree(REPO, leakscan.patterns_for(None), files, honour_planted_marker=True)
    assert leaks == [], leakscan.format_hits(leaks)
    secrets, _ = secretscan.scan(REPO, files, honour_planted_marker=True)
    assert [h for h in secrets if not h.pattern.startswith("gitleaks")] == []
