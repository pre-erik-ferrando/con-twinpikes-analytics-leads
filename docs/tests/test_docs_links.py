"""No .md in the repo links to a file that does not exist. No DB."""
import os
import re
import urllib.parse

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SKIPPED_DIRS = {".git", "target", "dbt_packages", "__pycache__", ".ingest_cache",
                ".pytest_cache", ".venv", "venv", "node_modules"}

LINK = re.compile(r"\]\(([^)\s]+?)(#[^)]*)?\)")


def _markdowns():
    for base, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIPPED_DIRS]
        for fn in files:
            if fn.endswith(".md"):
                yield os.path.join(base, fn)


def test_no_md_links_to_a_missing_file():
    broken = []
    for path in _markdowns():
        folder = os.path.dirname(path)
        text = open(path, encoding="utf-8", errors="ignore").read()
        for target, _anchor in LINK.findall(text):
            if re.match(r"^(https?:|mailto:|#|<)", target):
                continue
            target = urllib.parse.unquote(target)
            if not os.path.exists(os.path.normpath(os.path.join(folder, target))):
                broken.append(f"{os.path.relpath(path, REPO)} → {target}")
    assert not broken, (
        "relative links that resolve to no file:\n  " + "\n  ".join(broken[:15])
        + (f"\n  … and {len(broken) - 15} more" if len(broken) > 15 else "")
    )


def test_no_md_links_to_a_missing_anchor_in_ANOTHER_file():
    ids_of = {}
    broken = []
    for path in _markdowns():
        folder = os.path.dirname(path)
        text = open(path, encoding="utf-8", errors="ignore").read()
        for target, anchor in LINK.findall(text):
            if not anchor or re.match(r"^(https?:|mailto:|#|<)", target):
                continue   # internal `](#x)` links have their own test
            target = urllib.parse.unquote(target)
            target_md = os.path.normpath(os.path.join(folder, target))
            if not (target_md.endswith(".md") and os.path.exists(target_md)):
                continue   # not a repo .md, or missing (the test above reports it)
            if target_md not in ids_of:
                ids_of[target_md] = set(re.findall(
                    r'^<a id="([^"]+)"></a>$', open(target_md, encoding="utf-8", errors="ignore").read(), re.M))
            if anchor[1:] not in ids_of[target_md]:
                broken.append(f"{os.path.relpath(path, REPO)} → {target}{anchor}")
    assert not broken, (
        "links to an anchor that does not exist in the target file:\n  " + "\n  ".join(broken[:15])
        + (f"\n  … and {len(broken) - 15} more" if len(broken) > 15 else "")
    )


# Numbered section ids (`AM2`, `22.4`), resolved against any heading or anchor in any .md.
CITATION_ID = re.compile(r"§\s*([A-Z]{1,3}[0-9]{0,2}|[0-9]{1,2}[a-z]?(?:\.[0-9]{1,2})?)\b")
# `## AM. …` and `### 22.4 · …`: the two heading shapes.
HEADING_ID = re.compile(r"^#{1,4}\s+([A-Za-z0-9.]+)\s*[.·]", re.M)


def test_section_id_citations_resolve():
    ids = set()
    for path in _markdowns():
        text = open(path, encoding="utf-8", errors="ignore").read()
        ids |= {a.lower() for a in re.findall(r'^<a id="([^"]+)"></a>$', text, re.M)}
        for m in HEADING_ID.finditer(text):
            # `22.4` is also anchored as `22-4`: both forms count.
            ids.add(m.group(1).lower())
            ids.add(m.group(1).lower().replace(".", "-"))
    dead = []
    for path in _markdowns():
        for n, line in enumerate(open(path, encoding="utf-8", errors="ignore").read().splitlines(), 1):
            for m in CITATION_ID.finditer(line):
                sec = m.group(1).lower()
                if sec in ids or sec.replace(".", "-") in ids:
                    continue
                dead.append(f"{os.path.relpath(path, REPO)}:{n} → §{m.group(1)}")
    assert not dead, (
        "citation(s) to a § that exists in no .md:\n  " + "\n  ".join(dead[:15])
        + (f"\n  … and {len(dead) - 15} more" if len(dead) > 15 else "")
        + "\n\nEither the entry was renumbered (update the citation), or the section was removed"
          " (cite what replaced it), or it never existed."
    )
