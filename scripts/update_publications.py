#!/usr/bin/env python3
"""Update only the marked publication list using arXiv's official Atom API.

Python 3.11+, standard library only. Failed or empty fetches never replace the
current list. Successful fetches are merged by unversioned arXiv ID so older
papers are retained if arXiv temporarily omits a result.
"""

import argparse
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
import sys
import tempfile
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ATOM = "http://www.w3.org/2005/Atom"
ARXIV = "http://arxiv.org/schemas/atom"
OPEN_SEARCH = "http://a9.com/-/spec/opensearch/1.1/"
API_URL = "https://export.arxiv.org/api/query"
START = "<!-- ARXIV:START -->"
END = "<!-- ARXIV:END -->"
PAGE_SIZE = 100
MAX_PAPERS = 1000
MAX_BYTES = 5_000_000
MIN_INTERVAL = 3.2
ID_PATTERN = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-zA-Z][a-zA-Z.\-]*/\d{7})(?:v[1-9]\d*)?$")


class SyncError(Exception):
    """The updater cannot safely replace the publication list."""


def text(element, path):
    return " ".join((element.findtext(path) or "").split())


def normalized_name(name):
    name = unicodedata.normalize("NFKC", name).casefold()
    return " ".join(name.replace(".", "").split())


def matches_author(authors, names):
    allowed = {normalized_name(name) for name in names}
    return any(normalized_name(name) in allowed for name in authors)


def canonical_id(value):
    if "://" in value:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or parsed.hostname not in ("arxiv.org", "www.arxiv.org"):
            raise SyncError("Unexpected arXiv identifier URL")
        if not parsed.path.startswith("/abs/"):
            raise SyncError("Unexpected arXiv identifier path")
        value = parsed.path[5:]
    if not ID_PATTERN.fullmatch(value):
        raise SyncError("Invalid arXiv identifier")
    return re.sub(r"v[1-9]\d*$", "", value)


def valid_date(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Timezone required")
        return value
    except (ValueError, AttributeError) as error:
        raise SyncError("Invalid arXiv publication date") from error


def parse_feed(payload, author_names):
    if len(payload) > MAX_BYTES or re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", payload, re.I):
        raise SyncError("Unsupported or oversized arXiv response")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise SyncError("Malformed arXiv response") from error
    if root.tag != f"{{{ATOM}}}feed":
        raise SyncError("arXiv did not return an Atom feed")
    try:
        total = int(root.findtext(f"{{{OPEN_SEARCH}}}totalResults"))
        start = int(root.findtext(f"{{{OPEN_SEARCH}}}startIndex"))
    except (TypeError, ValueError) as error:
        raise SyncError("arXiv returned an error or incomplete feed metadata") from error
    if not 0 <= total <= MAX_PAPERS or start < 0:
        raise SyncError("Unexpected result count; keep the current list and review the query")
    entries = root.findall(f"{{{ATOM}}}entry")
    papers = []
    for entry in entries:
        identifier = canonical_id(text(entry, f"{{{ATOM}}}id"))
        authors = [text(author, f"{{{ATOM}}}name") for author in entry.findall(f"{{{ATOM}}}author")]
        if not matches_author(authors, author_names):
            continue
        title = text(entry, f"{{{ATOM}}}title")
        if not title or not all(authors):
            raise SyncError("Incomplete arXiv paper metadata")
        category = entry.find(f"{{{ARXIV}}}primary_category")
        papers.append({
            "id": identifier,
            "title": title,
            "authors": authors,
            "published": valid_date(text(entry, f"{{{ATOM}}}published")),
            "updated": valid_date(text(entry, f"{{{ATOM}}}updated")),
            "primary_category": category.get("term", "") if category is not None else "",
            "journal_ref": text(entry, f"{{{ARXIV}}}journal_ref"),
            "doi": text(entry, f"{{{ARXIV}}}doi"),
        })
    return papers, total, start, len(entries)


def fetch_publications(config):
    papers, offset, previous_request = [], 0, None
    while True:
        url = API_URL + "?" + urlencode({
            "search_query": config["search_query"], "start": offset,
            "max_results": PAGE_SIZE, "sortBy": "submittedDate", "sortOrder": "descending",
        })
        for attempt in range(3):
            if previous_request is not None:
                time.sleep(max(0, MIN_INTERVAL - (time.monotonic() - previous_request)))
            previous_request = time.monotonic()
            try:
                request = Request(url, headers={
                    "User-Agent": "SamborskaAcademicWebsite/1.0 (contact: liubovs@umich.edu)",
                    "Accept": "application/atom+xml",
                })
                with urlopen(request, timeout=25) as response:
                    payload = response.read(MAX_BYTES + 1)
                batch, total, received_start, count = parse_feed(payload, config["author_names"])
                break
            except (HTTPError, URLError, TimeoutError, OSError) as error:
                if attempt == 2 or (isinstance(error, HTTPError) and error.code not in (429, 500, 502, 503, 504)):
                    raise SyncError("Could not fetch arXiv; existing publications were kept") from error
                time.sleep(MIN_INTERVAL * (attempt + 1))
        if received_start != offset or (offset < total and count == 0):
            raise SyncError("Incomplete arXiv pagination; existing publications were kept")
        papers.extend(batch)
        offset += count
        if offset >= total:
            break
    if not papers:
        raise SyncError("arXiv returned no matching papers; existing publications were kept")
    return papers


def merge_papers(previous, incoming, config):
    merged = {}
    excluded = {canonical_id(value) for value in config.get("exclude_ids", [])}
    for paper in [*previous, *incoming]:
        identifier = canonical_id(paper["id"])
        if identifier in excluded or not matches_author(paper["authors"], config["author_names"]):
            continue
        old = merged.get(identifier)
        if old is None or datetime.fromisoformat(paper["updated"].replace("Z", "+00:00")) >= datetime.fromisoformat(old["updated"].replace("Z", "+00:00")):
            merged[identifier] = dict(paper, id=identifier)
    return sorted(merged.values(), key=lambda paper: (paper["published"], paper["id"]), reverse=True)


def bibtex(paper):
    key = "arxiv" + re.sub(r"[^a-zA-Z0-9]", "", paper["id"])
    fields = {
        "title": paper["title"], "author": " and ".join(paper["authors"]),
        "year": paper["published"][:4], "eprint": paper["id"], "archivePrefix": "arXiv",
        "primaryClass": paper["primary_category"],
        "doi": "10.48550/arXiv." + paper["id"],
        "url": "https://arxiv.org/abs/" + paper["id"],
    }
    return "@misc{" + key + ",\n" + ",\n".join(f"  {name} = {{{value}}}" for name, value in fields.items() if value) + "\n}"


def render_publications(papers, config):
    parts = []
    escape = html.escape
    for paper in papers:
        identifier = paper["id"]
        override = config.get("overrides", {}).get(identifier, {})
        heading = "paper-" + re.sub(r"[^a-zA-Z0-9]", "-", identifier)
        url = "https://arxiv.org/abs/" + identifier
        pdf = "https://arxiv.org/pdf/" + identifier
        venue = override.get("venue") or ("Published" if paper.get("journal_ref") else "arXiv " + paper["published"][:4])
        authors = []
        for author in paper["authors"]:
            author_html = escape(author)
            authors.append(f"<strong>{author_html}</strong>" if matches_author([author], config["author_names"]) else author_html)
        author_line = authors[0] if len(authors) == 1 else ", ".join(authors[:-1]) + " and " + authors[-1]
        description = override.get("description_html", "")  # Trusted, owner-edited local content only.
        description_html = f'<p class="paper-description">{description}</p>' if description else ""
        journal = f'<p class="paper-description">{escape(paper["journal_ref"])}</p>' if paper.get("journal_ref") else ""
        conference = override.get("conference_url", "")
        if conference and (urlparse(conference).scheme != "https" or not urlparse(conference).netloc):
            raise SyncError("Conference links must be HTTPS URLs")
        conference_link = f'<a href="{escape(conference, quote=True)}">Conference <span aria-hidden="true">↗</span></a>' if conference else ""
        parts.append(f'''          <article class="publication" aria-labelledby="{heading}">
            <div class="paper-meta"><span class="venue">{escape(venue)}</span></div>
            <h3 id="{heading}"><a href="{escape(url, quote=True)}">{escape(paper['title'])}</a></h3>
            <p class="authors">{author_line}</p>
            {description_html}
            {journal}
            <div class="paper-links">
              <a href="{escape(pdf, quote=True)}" aria-label="Read the PDF of {escape(paper['title'], quote=True)}">PDF <span aria-hidden="true">↗</span></a>
              <a href="{escape(url, quote=True)}" aria-label="View {escape(paper['title'], quote=True)} on arXiv">arXiv <span aria-hidden="true">↗</span></a>
              {conference_link}
            </div>
            <details class="citation">
              <summary>BibTeX</summary>
              <pre tabindex="0" aria-label="BibTeX citation">{escape(bibtex(paper))}</pre>
            </details>
          </article>''')
    return "\n".join(parts)


def update_site(site_dir, config, incoming=None):
    index_path = site_dir / "index.html"
    cache_path = site_dir / "publications.json"
    page = index_path.read_text(encoding="utf-8")
    if page.count(START) != 1 or page.count(END) != 1 or page.index(START) >= page.index(END):
        raise SyncError("Publication markers are missing or duplicated; nothing was changed")
    old_cache = cache_path.read_text(encoding="utf-8") if cache_path.exists() else ""
    previous = json.loads(old_cache).get("papers", []) if old_cache else []
    papers = merge_papers(previous, fetch_publications(config) if incoming is None else incoming, config)
    if not papers:
        raise SyncError("No publications to display; nothing was changed")
    generated = render_publications(papers, config)
    before, rest = page.split(START, 1)
    _, after = rest.split(END, 1)
    updated_page = before + START + "\n" + generated + "\n          " + END + after
    updated_cache = json.dumps({"schema_version": 1, "papers": papers}, ensure_ascii=False, indent=2) + "\n"
    pending = []
    try:
        for path, old, new in [(cache_path, old_cache, updated_cache), (index_path, page, updated_page)]:
            if old == new:
                continue
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=site_dir, delete=False) as handle:
                handle.write(new)
                pending.append((Path(handle.name), path))
        changed = bool(pending)
        for temporary, target in pending:
            temporary.replace(target)
    finally:
        for temporary, _ in pending:
            temporary.unlink(missing_ok=True)
    return {"changed": changed, "paper_count": len(papers), "ids": [p["id"] for p in papers]}


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-dir", type=Path, default=root / "dist" if (root / "dist/index.html").exists() else root)
    parser.add_argument("--config", type=Path, default=root / "arxiv-config.json")
    parser.add_argument("--offline", action="store_true", help="Render only the saved metadata without contacting arXiv")
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if not config.get("author_names") or not config.get("search_query"):
            raise SyncError("The author search is not configured")
        result = update_site(args.site_dir.resolve(), config, [] if args.offline else None)
        result["checked_at"] = datetime.now(timezone.utc).isoformat()
        print(json.dumps(result))
    except (SyncError, OSError, ValueError, KeyError, TypeError) as error:
        print("Publication update failed: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
