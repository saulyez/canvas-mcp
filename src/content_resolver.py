"""Walk a course's modules and collect every Canvas file the user can reach from them.

Canvas never stores files *in* modules. Module items reference content; files are
attached to that content in different ways depending on item type:

    File         content_id is the file id
    Page         file links inside the page body HTML
    Assignment   file links inside the description HTML
    Discussion   file links inside the message HTML + topic attachments
    Quiz         file links inside the description HTML
    ExternalUrl  only counts when it points at a file on this Canvas host
    ExternalTool / SubHeader   never carry files

Pages linked from module content but not themselves module items are followed
``follow_depth`` hops (needed for "flat template" courses). Pages that ARE module
items are never followed from elsewhere so their files stay with their own module.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from .html_links import ExtractedLinks, PageLink, extract_links, parse_file_url

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileRef:
    file_id: int
    module_id: int
    module_name: str
    module_position: int
    item_position: int
    source_type: str  # File | Page | Assignment | Discussion | Quiz | ExternalUrl | LinkedPage
    source_title: str
    subheader: Optional[str] = None
    via_link: bool = False  # found on a page reached by following a link, not a module item

    @property
    def priority(self) -> tuple[int, int, int]:
        """Lower wins when the same file is referenced from several places."""
        return (int(self.via_link), self.module_position, self.item_position)


@dataclass
class ResolveReport:
    refs: list[FileRef] = field(default_factory=list)
    modules_scanned: int = 0
    item_types: Counter = field(default_factory=Counter)
    skipped_items: list[dict[str, Any]] = field(default_factory=list)
    external_links_skipped: int = 0
    pages_followed: int = 0


def _http_status(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http {exc.response.status_code}"
    return type(exc).__name__


class ContentResolver:
    def __init__(self, client: Any, course_id: int | str, follow_depth: int = 1):
        self.client = client
        self.course_id = int(course_id)
        self.follow_depth = follow_depth
        self.canvas_host = urlparse(client.base_url).hostname
        self._page_cache: dict[str, Optional[dict]] = {}

    # ------------------------------------------------------------------ fetch
    async def _modules(self) -> list[dict]:
        cid = self.course_id
        modules = await self.client.get_paginated(
            "GET",
            f"courses/{cid}/modules",
            params={"include[]": ["items", "content_details"], "per_page": 100},
        )
        modules = sorted(modules, key=lambda m: m.get("position") or 0)
        for m in modules:
            # Canvas omits inline items for modules it deems too large.
            if m.get("items") is None:
                m["items"] = await self.client.get_paginated(
                    "GET",
                    f"courses/{cid}/modules/{m['id']}/items",
                    params={"include[]": ["content_details"], "per_page": 100},
                )
        return modules

    async def _page(self, slug: str) -> Optional[dict]:
        if slug not in self._page_cache:
            try:
                self._page_cache[slug] = await self.client.get_json(
                    f"courses/{self.course_id}/pages/{slug}"
                )
            except httpx.HTTPStatusError as exc:
                log.warning("page %s: %s", slug, _http_status(exc))
                self._page_cache[slug] = None
        return self._page_cache[slug]

    # ---------------------------------------------------------------- resolve
    async def resolve(self) -> ResolveReport:
        rep = ResolveReport()
        modules = await self._modules()
        rep.modules_scanned = len(modules)

        # Slugs of every page that is itself a module item (never follow into these).
        module_pages = {
            it.get("page_url")
            for m in modules
            for it in m["items"]
            if it.get("type") == "Page" and it.get("page_url")
        }
        followed: set[str] = set()

        for m in modules:
            subheader: Optional[str] = None
            for it in sorted(m["items"], key=lambda i: i.get("position") or 0):
                itype = it.get("type") or "Unknown"
                rep.item_types[itype] += 1
                title = it.get("title") or ""

                def ref(fid: int, source: str, src_title: str = title, via: bool = False) -> FileRef:
                    return FileRef(
                        file_id=fid,
                        module_id=m["id"],
                        module_name=m.get("name") or f"module_{m['id']}",
                        module_position=m.get("position") or 0,
                        item_position=it.get("position") or 0,
                        source_type=source,
                        source_title=src_title,
                        subheader=subheader,
                        via_link=via,
                    )

                def skip(reason: str) -> None:
                    rep.skipped_items.append(
                        {"module": m.get("name"), "item": title, "type": itype, "reason": reason}
                    )

                if itype == "SubHeader":
                    subheader = title
                    continue
                if itype == "ExternalTool":
                    rep.external_links_skipped += 1
                    continue
                if itype == "ExternalUrl":
                    fid = parse_file_url(it.get("external_url") or "", self.canvas_host)
                    if fid is not None:
                        rep.refs.append(ref(fid, "ExternalUrl"))
                    else:
                        rep.external_links_skipped += 1
                    continue
                if (it.get("content_details") or {}).get("locked_for_user"):
                    skip("locked")
                    continue

                try:
                    links: Optional[ExtractedLinks] = None
                    if itype == "File":
                        if it.get("content_id"):
                            rep.refs.append(ref(int(it["content_id"]), "File"))
                    elif itype == "Page":
                        page = await self._page(it.get("page_url") or "")
                        if page is None:
                            skip("page unavailable")
                            continue
                        if page.get("locked_for_user"):
                            skip("locked")
                            continue
                        links = extract_links(page.get("body"), self.canvas_host)
                    elif itype == "Assignment":
                        a = await self.client.get_json(
                            f"courses/{self.course_id}/assignments/{it['content_id']}"
                        )
                        links = extract_links(a.get("description"), self.canvas_host)
                    elif itype == "Discussion":
                        d = await self.client.get_json(
                            f"courses/{self.course_id}/discussion_topics/{it['content_id']}"
                        )
                        links = extract_links(d.get("message"), self.canvas_host)
                        for att in d.get("attachments") or []:
                            if att.get("id") and int(att["id"]) not in links.file_ids:
                                links.file_ids.append(int(att["id"]))
                    elif itype == "Quiz":
                        q = await self.client.get_json(
                            f"courses/{self.course_id}/quizzes/{it['content_id']}"
                        )
                        links = extract_links(q.get("description"), self.canvas_host)
                    else:
                        skip("unsupported item type")
                        continue
                except httpx.HTTPStatusError as exc:
                    skip(_http_status(exc))
                    continue

                if links is None:
                    continue
                for fid in links.file_ids:
                    rep.refs.append(ref(fid, itype))
                await self._follow(
                    links.pages, 1, module_pages, followed, rep, lambda fid, t: ref(fid, "LinkedPage", t, True)
                )
        return rep

    async def _follow(self, pages: list[PageLink], depth: int, module_pages: set, followed: set,
                      rep: ResolveReport, make_ref) -> None:
        if depth > self.follow_depth:
            return
        for p in pages:
            if p.course_id != self.course_id or p.slug in module_pages or p.slug in followed:
                continue
            followed.add(p.slug)
            page = await self._page(p.slug)
            if page is None or page.get("locked_for_user"):
                continue
            # A link may use an old slug or id; compare against the canonical url too.
            canonical = page.get("url")
            if canonical and canonical in module_pages:
                continue
            if canonical:
                followed.add(canonical)
            rep.pages_followed += 1
            links = extract_links(page.get("body"), self.canvas_host)
            title = page.get("title") or p.slug
            for fid in links.file_ids:
                rep.refs.append(make_ref(fid, title))
            await self._follow(links.pages, depth + 1, module_pages, followed, rep, make_ref)
