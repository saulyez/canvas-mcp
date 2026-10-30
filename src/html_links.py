"""Extract Canvas file and page references from Canvas-authored HTML (page bodies,
assignment descriptions, discussion messages, quiz descriptions).

Only *linked* content is reported: inline <img> embeds (screenshots, banners, icons)
are deliberately ignored, per the download-tool plan.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import unquote, urlparse

# /courses/1/files/2, /api/v1/courses/1/files/2, /files/2, /courses/1/files/2/download?...
FILE_RE = re.compile(r"/(?:api/v1/)?(?:courses/(\d+)/)?files/(\d+)(?=[/?#]|$)")
# /courses/1/pages/slug, /api/v1/courses/1/pages/slug
PAGE_RE = re.compile(r"/(?:api/v1/)?courses/(\d+)/pages/([^/?#]+)")

# Tags whose URL attributes count as "linked" content. <img> is intentionally absent.
_LINK_ATTRS: dict[str, tuple[str, ...]] = {
    "a": ("href", "data-api-endpoint"),
    "iframe": ("src", "data-api-endpoint"),
    "embed": ("src",),
    "object": ("data",),
    "source": ("src",),
    "video": ("src",),
    "audio": ("src",),
}


@dataclass(frozen=True)
class PageLink:
    course_id: int
    slug: str


@dataclass
class ExtractedLinks:
    """File ids and page links in document order, de-duplicated."""

    file_ids: list[int] = field(default_factory=list)
    pages: list[PageLink] = field(default_factory=list)


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        wanted = _LINK_ATTRS.get(tag.lower())
        if not wanted:
            return
        for name, value in attrs:
            if value and name.lower() in wanted:
                self.urls.append(value)

    handle_startendtag = handle_starttag


def _same_host(url: str, canvas_host: Optional[str]) -> bool:
    host = urlparse(url).hostname
    if host is None:  # relative link -> same Canvas instance
        return True
    return canvas_host is None or host.lower() == canvas_host.lower()


def parse_file_url(url: str, canvas_host: Optional[str] = None) -> Optional[int]:
    """Return the Canvas file id referenced by ``url``, or None."""
    url = html.unescape(url).strip()
    if not _same_host(url, canvas_host):
        return None
    parsed = urlparse(url)
    m = FILE_RE.search(parsed.path)
    if m:
        return int(m.group(2))
    # Files-tab preview links: /courses/1/files?preview=123
    if parsed.path.rstrip("/").endswith("/files"):
        pm = re.search(r"(?:^|&)preview=(\d+)(?:&|$)", parsed.query)
        if pm:
            return int(pm.group(1))
    return None


def parse_page_url(url: str, canvas_host: Optional[str] = None) -> Optional[PageLink]:
    """Return the course page referenced by ``url``, or None."""
    url = html.unescape(url).strip()
    if not _same_host(url, canvas_host):
        return None
    m = PAGE_RE.search(urlparse(url).path)
    if not m:
        return None
    return PageLink(course_id=int(m.group(1)), slug=unquote(m.group(2)))


def extract_links(body: Optional[str], canvas_host: Optional[str] = None) -> ExtractedLinks:
    """Find linked Canvas files and pages in an HTML fragment."""
    out = ExtractedLinks()
    if not body:
        return out
    collector = _Collector()
    collector.feed(body)
    collector.close()

    seen_files: set[int] = set()
    seen_pages: set[PageLink] = set()
    for raw in collector.urls:
        fid = parse_file_url(raw, canvas_host)
        if fid is not None:
            if fid not in seen_files:
                seen_files.add(fid)
                out.file_ids.append(fid)
            continue
        page = parse_page_url(raw, canvas_host)
        if page is not None and page not in seen_pages:
            seen_pages.add(page)
            out.pages.append(page)
    return out
