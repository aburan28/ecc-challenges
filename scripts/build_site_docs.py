"""Build the site's documentation from reviewed repository Markdown.

Run after editing a published document: python scripts/build_site_docs.py
CI checks the generated bundle. No Markdown parser or repository access is
needed in the web image; only this allowlisted bundle is served.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import posixpath
import re
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "src/ecc_challenges/site_docs.json"
DOCUMENTS = {
    "README.md": "project",
    "CONTRIBUTING.md": "contributing",
    "skills/ecc-challenges-worker/references/prerequisites.md": "worker-prerequisites",
    **{f"docs/{name}.md": name for name in (
        "running-a-worker", "agents", "modal-workers", "local-stack", "campaigns",
        "admission-and-capacity", "multicurve-storage", "operations",
    )},
    "problems/README.md": "problems",
    "problems/dp32-verifier-and-ingest-capacity.md": "problems/dp32-verifier-and-ingest-capacity",
    "problems/multi-campaign-worker-and-verifier.md": "problems/multi-campaign-worker-and-verifier",
}


def site_link(href: str, source: str) -> str | None:
    """Resolve document links locally; do not render GitHub navigation links."""
    url = urlsplit(href)
    host = (url.hostname or "").lower()
    if host == "ecc-challenges.com":
        return urlunsplit(("", "", url.path or "/", url.query, url.fragment))
    if host == "github.com" or host.endswith(".github.com") or host == "raw.githubusercontent.com":
        path = unquote(url.path)
        for prefix in ("/aburan28/ecc-challenges/blob/main/", "/aburan28/ecc-challenges/tree/main/",
                       "/aburan28/ecc-challenges/main/"):
            if path.startswith(prefix):
                document = path.removeprefix(prefix).rstrip("/")
                if document == "problems":
                    document = "problems/README.md"
                slug = DOCUMENTS.get(document)
                if slug is None:
                    return None
                return f"/docs/{slug}" + (f"#{quote(unquote(url.fragment))}" if url.fragment else "")
        if path.rstrip("/") == "/aburan28/ecc-challenges":
            return "/docs/project"
        return None
    if url.scheme or url.netloc or not url.path or url.path.startswith("/"):
        return href
    document = posixpath.normpath(posixpath.join(posixpath.dirname(source), unquote(url.path)))
    slug = DOCUMENTS.get(document)
    if slug is None:
        # Receipts and source files are still named in the text. Do not invent
        # routes for files which this documentation bundle does not publish.
        return None
    return f"/docs/{slug}" + (f"#{quote(unquote(url.fragment))}" if url.fragment else "")


def render_document(source: str, markdown: str) -> dict:
    source_sha256 = hashlib.sha256(markdown.encode()).hexdigest()
    # Operational examples contain account-specific IDs. The public reading
    # copy uses placeholders; repository documents remain byte-for-byte intact.
    public_markdown = re.sub(
        r"(\baccount\s+|--expected-account\s+|ecc-challenges-data-)[0-9]{12}\b",
        r"\g<1>ACCOUNT_ID", markdown,
    )
    parser = MarkdownIt("commonmark", {"html": False}).enable("table")
    tokens = parser.parse(public_markdown)
    title = ""
    toc = []
    seen = {}
    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            inline = tokens[index + 1]
            label = "".join(child.content for child in inline.children or []
                            if child.type in {"text", "code_inline"})
            anchor = re.sub(r"[^\w\s-]", "", label.lower()).replace(" ", "-")
            count = seen.get(anchor, 0)
            seen[anchor] = count + 1
            anchor += f"-{count}" if count else ""
            token.attrSet("id", anchor)
            if token.tag == "h1" and not title:
                title = label
            if token.tag == "h2":
                toc.append({"title": label, "id": anchor})
        if token.type == "table_open":
            token.type = "html_block"
            token.content = '<div class="table-wrap"><table>\n'
        elif token.type == "table_close":
            token.type = "html_block"
            token.content = "</table></div>\n"
        if token.children:
            links = []
            for child in token.children:
                if child.type == "link_open":
                    target = site_link(child.attrGet("href") or "", source)
                    links.append(target is None)
                    if target is None:
                        child.hidden = True
                    else:
                        child.attrSet("href", target)
                elif child.type == "link_close" and links.pop():
                    child.hidden = True
    return {"title": title, "source": source, "account_placeholders": public_markdown != markdown,
            "source_sha256": source_sha256,
            "toc": toc, "html": parser.renderer.render(tokens, parser.options, {})}


def build_bundle() -> str:
    pages = {slug: render_document(source, (ROOT / source).read_text())
             for source, slug in DOCUMENTS.items()}
    # Ship the canonical contributor skill in the existing package/image asset.
    pages["agents"]["skill_markdown"] = (ROOT / "skills/ecc-challenges-worker/SKILL.md").read_text()
    return json.dumps(pages, ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the committed bundle is stale")
    args = parser.parse_args()
    bundle = build_bundle()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text() != bundle:
            raise SystemExit("Site documentation is stale: run python scripts/build_site_docs.py")
        print(f"Site documentation is current ({len(DOCUMENTS)} pages)")
    else:
        OUTPUT.write_text(bundle)
        print(f"Wrote {len(DOCUMENTS)} pages to {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
