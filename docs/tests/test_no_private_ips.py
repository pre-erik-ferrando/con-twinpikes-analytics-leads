"""No hand-written DB host anywhere under version control. No DB."""
import os
import re
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PRIVATE_IP = re.compile(
    r"\b(?:"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r")\b"
)

_IPV4 = r"\d{1,3}(?:\.\d{1,3}){3}"
# An IPv4 in host position, whatever its range: anchored on the position, so no allowlist is needed.
IN_HOST = re.compile(
    r"(?i:host(?:name)?|server)\s*[=:]\s*[\"']?(" + _IPV4 + r")"
    r"|//(" + _IPV4 + r")[:/]"
    r"|@(" + _IPV4 + r")[:/]"
)

# Excluded: this file holds the patterns.
THIS_FILE = os.path.relpath(os.path.abspath(__file__), REPO)


# Without git (e.g. a `git archive` copy) the disk is walked, skipping these; `logs/` and `data/`
# are never committed.
SKIPPED_DIRS = {".git", "target", "dbt_packages", "__pycache__", ".ingest_cache", ".pytest_cache",
           ".venv", "venv", "node_modules", "logs", "data"}


def _versionables():
    try:
        files = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=REPO, capture_output=True, text=True, check=True).stdout.splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = []
        for base, dirs, names in os.walk(REPO):
            dirs[:] = [d for d in dirs if d not in SKIPPED_DIRS]
            for fn in names:
                files.append(os.path.relpath(os.path.join(base, fn), REPO))
    for rel in files:
        if rel == THIS_FILE:
            continue
        path = os.path.join(REPO, rel)
        if os.path.isfile(path):
            yield rel, path


def test_no_hand_written_private_ip():
    found = []
    for rel, path in _versionables():
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        if b"\x00" in raw[:8000]:          # binary
            continue
        text = raw.decode("utf-8", errors="ignore")
        for ip in set(PRIVATE_IP.findall(text)):
            found.append(f"{rel} → {ip} (private range)")
        # Three alternatives: the matching group is the only non-empty one.
        for m in IN_HOST.finditer(text):
            ip = next(g for g in m.groups() if g)
            found.append(f"{rel} → {ip} (host position)")
    found = sorted(set(found))
    assert not found, (
        "hand-written DB host (it belongs in DBT_CH_HOST, not in the repo):\n  "
        + "\n  ".join(found[:15])
        + (f"\n  … and {len(found) - 15} more" if len(found) > 15 else "")
    )
