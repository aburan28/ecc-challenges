"""Documentation links must work inside the site without a database or GitHub."""

from html.parser import HTMLParser
import re
from urllib.parse import unquote, urljoin, urlsplit

from fastapi.testclient import TestClient
import pytest
from starlette.requests import Request

from ecc_challenges.app import TEMPLATES
from ecc_challenges.catalog import CHALLENGES
from ecc_challenges.pages import DOCUMENTS
from scripts.build_site_docs import OUTPUT, build_bundle, render_document
from test_catalog import application
from test_results import UnavailableDB


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.hrefs = []
        self.ids = set()
        self.tags = set()
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        attrs = dict(attrs)
        if tag == "a" and "href" in attrs:
            self.hrefs.append(attrs["href"])
        if "id" in attrs:
            self.ids.add(attrs["id"])


def test_documentation_bundle_matches_its_sources():
    assert OUTPUT.read_text() == build_bundle(), "Run python scripts/build_site_docs.py"
    assert not re.search(r"\b[0-9]{12}\b", OUTPUT.read_text())


@pytest.mark.parametrize("slug", sorted(DOCUMENTS))
def test_each_document_renders_without_a_database(slug):
    with TestClient(application(db=UnavailableDB()), base_url="https://ecc-challenges.com") as client:
        response = client.get(f"/docs/{slug}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"].startswith("private,")
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert '<article class="document">' in response.text
    assert DOCUMENTS[slug]["html"] in response.text
    assert "h1" in Links(response.text).tags


def test_all_site_documentation_links_and_fragments_resolve():
    paths = ["/", "/status", "/docs", "/help", "/results/eccp-109"]
    paths += [f"/docs/{slug}" for slug in DOCUMENTS]
    with TestClient(application(), base_url="https://ecc-challenges.com") as client:
        pages = {path: Links(client.get(path).text) for path in paths}
        request = Request({"type": "http", "method": "GET", "path": "/dashboard", "query_string": b"",
                           "headers": [], "session": {"github_id": 1}, "scheme": "https",
                           "server": ("ecc-challenges.com", 443)})
        pages["/dashboard"] = Links(TEMPLATES.get_template("dashboard.html").render(
            request=request, login="me", keys=[], csrf="x", issued=None, challenges=CHALLENGES,
            worker_image="example.invalid/image@sha256:" + "a" * 64, multicurve_enabled=False,
            selected_campaign="ecc2k-130", client_os="linux"))
        for source, page in pages.items():
            for href in page.hrefs:
                link = urlsplit(urljoin("https://ecc-challenges.com" + source, href))
                host = link.hostname or ""
                assert host != "github.com" and not host.endswith(".github.com"), (source, href)
                assert host != "raw.githubusercontent.com", (source, href)
                if host != "ecc-challenges.com" or link.path in {"/auth/github", "/dashboard"}:
                    continue
                if link.path not in pages:
                    response = client.get(link.path)
                    assert response.status_code == 200, (source, href, response.status_code)
                    pages_for_target = Links(response.text)
                else:
                    pages_for_target = pages[link.path]
                if link.fragment:
                    assert unquote(link.fragment) in pages_for_target.ids, (source, href)


@pytest.mark.parametrize("path", ["/docs/unknown", "/docs/operations.md", "/docs/%2e%2e/pyproject.toml",
                                  "/docs/%2Fetc%2Fpasswd", "/docs/problems/missing"])
def test_document_routes_are_allowlisted(path):
    with TestClient(application(), base_url="https://ecc-challenges.com") as client:
        response = client.get(path)
    assert response.status_code == 404
    assert "Page not found" in response.text


def test_document_renderer_escapes_html_and_preserves_readable_content():
    document = render_document("docs/example.md", '''# Example

<script>alert("unsafe")</script>

[Worker](running-a-worker.md#macos)
[GitHub](https://github.com/aburan28/ecc-challenges)
[GitHub issue](https://github.com/aburan28/ecc-challenges/issues/123)
[Unsafe](javascript:alert(1))

```sh
echo '<img src=x onerror=alert(1)>'
```

## Repeat
## Repeat
''')
    html = document["html"]
    parsed = Links(html)
    assert "script" not in parsed.tags and "img" not in parsed.tags
    assert '/docs/running-a-worker#macos' in parsed.hrefs
    assert '/docs/project' in parsed.hrefs
    assert all(not href.startswith(("javascript:", "https://github.com/")) for href in parsed.hrefs)
    assert "GitHub issue" in html
    assert {"repeat", "repeat-1"} <= parsed.ids
