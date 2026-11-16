"""Publishes the Markdown pages of docs/outline/ to the Outline collection, one way: repo → Outline.

    python docs/outline/sync.py --dry-run         # renders the pages to docs/outline/_preview/, no network
    OUTLINE_API_TOKEN=... python docs/outline/sync.py
    OUTLINE_API_TOKEN=... python docs/outline/sync.py --no-create   # CI: updates only, never creates

What goes where: outline.toml. A page with `outline_id` takes over that existing Outline page (the
collection's template pages) and stays where it is; any other page is created the first time (its id is
saved in outline_ids.json, which is committed). Every run overwrites each page in full, so an edit made
in Outline is lost.
Links to another listed page become its Outline link; links to anything else in the repo become a
GitHub link. A page removed from outline.toml is NOT deleted in Outline: the run lists it.
Standard library only, so it runs anywhere Python 3.11+ does.
"""
import argparse
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
IDS_FILE = HERE / "outline_ids.json"
PREVIEW = HERE / "_preview"

LINK = re.compile(r"\]\(([^)\s]+?)(#[^)]*)?\)")
FENCE = re.compile(r"^ {0,3}(```|~~~)")
H1 = re.compile(r"\A# (.+)\n+")
BANNER = ("> Página generada desde el repositorio (`docs/outline/{file}`). **Los cambios hechos aquí se "
          "pierden en la siguiente sincronización**: edita el fichero del repo.\n\n")


class Outline:
    def __init__(self, url, token):
        self.url, self.token = url.rstrip("/"), token

    def call(self, method, **body):
        req = urllib.request.Request(
            f"{self.url}/api/{method}", data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)["data"]
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            sys.exit(f"[outline] {method} failed: HTTP {e.code} {e.read().decode(errors='replace')[:300]}")

    def must(self, method, **body):
        """call() for a page that has to exist: a 404 is an error, not a result."""
        data = self.call(method, **body)
        if data is None:
            sys.exit(f"[outline] {method} {body.get('id', '')}: not found in Outline (404)")
        return data


def load_pages(cfg):
    pages = cfg["page"]
    files = [p["file"] for p in pages]
    for p in pages:
        if not (HERE / p["file"]).is_file():
            sys.exit(f"[outline] {p['file']} is listed in outline.toml but does not exist")
        if p.get("outline_id") and p.get("parent"):
            sys.exit(f"[outline] {p['file']}: a page taken over with outline_id keeps its place, no parent")
        if not p.get("outline_id") and p.get("parent") not in files:
            sys.exit(f"[outline] {p['file']}: a new page needs a parent listed in outline.toml")
        if files.index(p.get("parent", p["file"])) > files.index(p["file"]):
            sys.exit(f"[outline] {p['file']}: list its parent {p['parent']} before it")
    return pages


def split_title(page):
    """(title, body): the first `# heading` is the page title in Outline, not part of its text."""
    text = (HERE / page["file"]).read_text(encoding="utf-8")
    m = H1.match(text)
    title = page.get("title") or (m.group(1).strip() if m else Path(page["file"]).stem)
    return title, text[m.end():] if m else text


def rewrite_links(page_file, body, cfg, page_urls):
    """Listed pages → their Outline URL; any other repo path → GitHub; external links untouched.
    Fenced code blocks are left as written."""
    folder = (HERE / page_file).parent

    def repl(m):
        target, anchor = m.group(1), m.group(2) or ""
        if re.match(r"^(https?:|mailto:|#)", target):
            return m.group(0)
        path = (folder / urllib.parse.unquote(target)).resolve()
        rel = path.relative_to(HERE).as_posix() if path.is_relative_to(HERE) else None
        if rel in page_urls:
            return f"]({page_urls[rel]})"   # Outline's heading anchors differ from GitHub's: dropped
        if not path.is_relative_to(REPO):
            sys.exit(f"[outline] {page_file}: link outside the repository: {target}")
        if not path.exists():
            sys.exit(f"[outline] {page_file}: link to a missing file: {target}")
        kind = "tree" if path.is_dir() else "blob"
        url_path = urllib.parse.quote(path.relative_to(REPO).as_posix())
        return f"]({cfg['github']}/{kind}/{cfg['branch']}/{url_path}{anchor})"

    out, fence = [], None
    for line in body.splitlines(keepends=True):
        m = FENCE.match(line)
        if fence is None and m:
            fence = m.group(1)
        elif fence is not None and m and m.group(1) == fence:
            fence = None
        elif fence is None:
            line = LINK.sub(repl, line)
        out.append(line)
    return "".join(out)


def render(page, cfg, page_urls):
    title, body = split_title(page)
    return title, BANNER.format(file=page["file"]) + rewrite_links(page["file"], body, cfg, page_urls)


def dry_run(cfg, pages):
    PREVIEW.mkdir(exist_ok=True)
    urls = {p["file"]: f"{cfg['url']}/doc/<{Path(p['file']).stem}>" for p in pages}
    for p in pages:
        title, text = render(p, cfg, urls)
        (PREVIEW / p["file"]).write_text(f"# {title}\n\n{text}", encoding="utf-8")
        where = "takes over its page" if p.get("outline_id") else f"new, under {p['parent']}"
        print(f"  {p['file']:<26} → {title!r} ({where})")
    print(f"[outline] dry run: {len(pages)} pages rendered to {PREVIEW.relative_to(REPO)}/, nothing sent")


def sync(cfg, pages, client, no_create=False):
    collection = client.call("collections.info", id=cfg["collection"])
    if not collection:
        sys.exit(f"[outline] collection {cfg['collection']} not found, or the token cannot see it")
    ids = json.loads(IDS_FILE.read_text()) if IDS_FILE.exists() else {}

    # Pass 1: every page exists (parents first, as listed), so pass 2 can link to any of them.
    docs = {}
    for p in pages:
        if p.get("outline_id"):
            doc = client.call("documents.info", id=p["outline_id"])
            if not doc:
                sys.exit(f"[outline] {p['file']}: page {p['outline_id']} not found in Outline")
        else:
            doc = client.call("documents.info", id=ids[p["file"]]) if p["file"] in ids else None
            if not doc:
                # CI cannot commit outline_ids.json back: a page it created would be created again next run.
                if no_create:
                    sys.exit(f"[outline] {p['file']} has no Outline page yet: run sync.py locally once "
                             "and commit outline_ids.json")
                doc = client.must("documents.create", title=split_title(p)[0], text="", publish=True,
                                  collectionId=collection["id"], parentDocumentId=docs[p["parent"]]["id"])
                print(f"  created  {p['file']}")
            if ids.get(p["file"]) != doc["id"]:
                ids[p["file"]] = doc["id"]
                # Saved at once: if a later call aborts the run, the next one finds this page instead
                # of creating it again.
                IDS_FILE.write_text(json.dumps(ids, indent=2) + "\n")
        docs[p["file"]] = doc

    # Pass 2: text and title; a new page also its parent and its order among the pages listed here.
    urls = {f: cfg["url"] + d["url"] for f, d in docs.items()}
    siblings = {}
    for p in pages:
        title, text = render(p, cfg, urls)
        doc = docs[p["file"]]
        client.must("documents.update", id=doc["id"], title=title, text=text, publish=True)
        if not p.get("outline_id"):
            parent = docs[p["parent"]]["id"]
            index = siblings.setdefault(parent, 0)
            client.must("documents.move", id=doc["id"], collectionId=collection["id"],
                        parentDocumentId=parent, index=index)
            siblings[parent] += 1
        print(f"  updated  {p['file']:<26} {urls[p['file']]}")

    for orphan in sorted(set(ids) - {p["file"] for p in pages}):
        print(f"  ⚠ {orphan} is no longer in outline.toml: its Outline page was left as is; delete it there "
              f"and remove it from outline_ids.json")
    print(f"[outline] {len(pages)} pages synced to {collection['name']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="render to docs/outline/_preview/, send nothing")
    ap.add_argument("--no-create", action="store_true", help="only update existing pages (for CI)")
    args = ap.parse_args()
    cfg = tomllib.loads((HERE / "outline.toml").read_text(encoding="utf-8"))
    pages = load_pages(cfg)
    if args.dry_run:
        return dry_run(cfg, pages)
    token = os.environ.get("OUTLINE_API_TOKEN")
    if not token:
        sys.exit("[outline] set OUTLINE_API_TOKEN (Outline → Settings → API)")
    sync(cfg, pages, Outline(cfg["url"], token), no_create=args.no_create)


if __name__ == "__main__":
    main()
