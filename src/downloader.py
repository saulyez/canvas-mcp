"""Download every Canvas file reachable from a course's modules.

Layout: <destination_root>/<course name>/<module name>/<file display_name>
A file referenced from several modules is stored once, in its "owner" module
(direct module reference beats a followed link, then earliest module/item);
the other references are listed in manifest.json.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from .client import AsyncCanvasClient, backoff_sleep, is_rate_limited, raise_for_canvas_status, MAX_RETRIES
from .content_resolver import ContentResolver, FileRef

log = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sanitize_name(name: Optional[str], fallback: str, max_len: int = 120) -> str:
    """Make a single safe path component (no separators, no traversal)."""
    s = html.unescape(name or "")
    s = _BAD_CHARS.sub("_", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"_{2,}", "_", s)
    s = s.strip(" ._")
    if not s or s in {".", ".."}:
        s = fallback
    if s.split(".")[0].upper() in _WINDOWS_RESERVED:
        s = f"_{s}"
    if len(s) > max_len:
        stem, dot, ext = s.rpartition(".")
        if dot and 0 < len(ext) <= 10:
            s = stem[: max_len - len(ext) - 1].rstrip(" ._") + "." + ext
        else:
            s = s[:max_len].rstrip(" ._")
    return s


def with_suffix_index(filename: str, n: int) -> str:
    stem, dot, ext = filename.rpartition(".")
    if dot and stem:
        return f"{stem} ({n}).{ext}"
    return f"{filename} ({n})"


@dataclass
class PlannedFile:
    file_id: int
    owner: FileRef
    also_in: list[FileRef]
    meta: dict[str, Any] = field(default_factory=dict)
    rel_path: Optional[Path] = None
    status: str = "pending"
    error: Optional[str] = None


class CanvasMaterialDownloader:
    """Handles downloading course materials from Canvas into a structured directory."""

    def __init__(
        self,
        client: AsyncCanvasClient,
        destination_root: str | os.PathLike,
        concurrency: int = 4,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.client = client
        self.destination_root = Path(destination_root).resolve()
        self.concurrency = concurrency
        self._transport = transport  # injectable for tests

    # ------------------------------------------------------------------ main
    async def download_course_materials(self, course_id: str | int, dry_run: bool = False) -> dict[str, Any]:
        course = await self.client.get_json(f"courses/{course_id}")
        course_name = course.get("name") or f"course_{course_id}"
        course_dir = self.destination_root / sanitize_name(course_name, f"course_{course_id}")

        report = await ContentResolver(self.client, course_id).resolve()
        planned = self._plan(report.refs)

        sem = asyncio.Semaphore(self.concurrency)
        await asyncio.gather(*(self._fetch_meta(course_id, p, sem) for p in planned))

        previous = self._load_manifest(course_dir)
        self._assign_paths(planned, course_dir, previous)

        if not dry_run:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(120, connect=15),
                transport=self._transport,
            ) as http:
                await asyncio.gather(*(self._download(http, course_dir, p, sem) for p in planned))
            self._write_manifest(course_dir, course_id, course_name, planned, report)
        else:
            for p in planned:
                if p.status == "pending":
                    p.status = "would_download"

        return self._summary(course_id, course_name, course_dir, planned, report, dry_run)

    # ------------------------------------------------------------------ plan
    @staticmethod
    def _plan(refs: list[FileRef]) -> list[PlannedFile]:
        by_id: dict[int, list[FileRef]] = defaultdict(list)
        for r in refs:
            by_id[r.file_id].append(r)
        planned = []
        for fid, rs in by_id.items():
            rs = sorted(rs, key=lambda r: r.priority)
            owner = rs[0]
            others = sorted(
                (r for r in rs[1:] if r.module_id != owner.module_id),
                key=lambda r: (r.module_position, r.item_position),
            )
            planned.append(PlannedFile(file_id=fid, owner=owner, also_in=others))
        planned.sort(key=lambda p: (p.owner.priority, p.file_id))
        return planned

    async def _fetch_meta(self, course_id: str | int, p: PlannedFile, sem: asyncio.Semaphore) -> None:
        async with sem:
            for path in (f"courses/{course_id}/files/{p.file_id}", f"files/{p.file_id}"):
                try:
                    p.meta = await self.client.get_json(path)
                    break
                except httpx.HTTPStatusError as exc:
                    p.error = f"metadata http {exc.response.status_code}"
            else:
                p.status = "skipped"
                return
        p.error = None
        if p.meta.get("locked_for_user"):
            p.status, p.error = "skipped", "locked"
        elif not p.meta.get("url"):
            p.status, p.error = "skipped", "no download url"

    def _assign_paths(self, planned: list[PlannedFile], course_dir: Path, previous: dict[int, str]) -> None:
        # Module folder names; disambiguate duplicates by module id.
        names: dict[int, str] = {}
        used: Counter = Counter()
        for p in planned:
            mid = p.owner.module_id
            if mid not in names:
                base = sanitize_name(p.owner.module_name, f"module_{mid}")
                used[base] += 1
                names[mid] = base if used[base] == 1 else f"{base} ({mid})"

        owners = {Path(v): k for k, v in previous.items()}
        taken: set[Path] = set()
        for p in planned:
            if p.status != "pending":
                continue
            module_dir = Path(names[p.owner.module_id])
            prior = previous.get(p.file_id)
            if prior and Path(prior).parent == module_dir and Path(prior) not in taken:
                rel = Path(prior)  # keep the name chosen on an earlier run
            else:
                fname = sanitize_name(
                    p.meta.get("display_name") or p.meta.get("filename"), f"file_{p.file_id}"
                )
                rel, n = module_dir / fname, 1
                while rel in taken or owners.get(rel) not in (None, p.file_id):
                    rel = module_dir / with_suffix_index(fname, n)
                    n += 1
            target = (course_dir / rel).resolve()
            if not target.is_relative_to(self.destination_root):
                p.status, p.error = "skipped", "unsafe path"
                continue
            taken.add(rel)
            p.rel_path = rel

    # -------------------------------------------------------------- download
    async def _download(self, http: httpx.AsyncClient, course_dir: Path, p: PlannedFile, sem: asyncio.Semaphore) -> None:
        if p.status != "pending" or p.rel_path is None:
            return
        target = course_dir / p.rel_path
        size = p.meta.get("size")
        existed = target.exists()
        if existed and size is not None and target.stat().st_size == size:
            p.status = "already_present"
            return

        part = target.with_name(target.name + ".part")
        async with sem:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                for attempt in range(MAX_RETRIES + 1):
                    headers = await self.client.auth.get_auth_headers()
                    # httpx drops Authorization when redirected to another origin (inst-fs).
                    async with http.stream("GET", p.meta["url"], headers=headers) as resp:
                        if is_rate_limited(resp) and attempt < MAX_RETRIES:
                            await resp.aread()
                            await backoff_sleep(attempt, resp)
                            continue
                        if resp.status_code >= 400:
                            await resp.aread()
                        raise_for_canvas_status(resp)
                        written = 0
                        with open(part, "wb") as fh:
                            async for chunk in resp.aiter_bytes(1 << 16):
                                fh.write(chunk)
                                written += len(chunk)
                    break
                if size is not None and written != size:
                    raise IOError(f"size mismatch: got {written} bytes, expected {size}")
                os.replace(part, target)
                p.status = "updated" if existed else "downloaded"
                log.info("downloaded %s", p.rel_path)
            except Exception as exc:  # one bad file must not abort the course
                part.unlink(missing_ok=True)
                p.status = "failed"
                p.error = f"{type(exc).__name__}: {exc}"
                log.warning("failed %s: %s", p.rel_path, p.error)

    # -------------------------------------------------------------- manifest
    @staticmethod
    def _load_manifest(course_dir: Path) -> dict[int, str]:
        try:
            data = json.loads((course_dir / MANIFEST_NAME).read_text())
            return {int(f["file_id"]): f["path"] for f in data.get("files", []) if f.get("path")}
        except (OSError, ValueError, KeyError, TypeError):
            return {}

    @staticmethod
    def _ref_dict(r: FileRef) -> dict[str, Any]:
        return {
            "module": r.module_name,
            "source_type": r.source_type,
            "source_title": r.source_title,
            "subheader": r.subheader,
            "via_link": r.via_link,
        }

    def _write_manifest(self, course_dir: Path, course_id, course_name, planned: list[PlannedFile], report) -> None:
        course_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "course_id": int(course_id),
            "course_name": course_name,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "files": [
                {
                    "file_id": p.file_id,
                    "display_name": p.meta.get("display_name"),
                    "content_type": p.meta.get("content-type"),
                    "size": p.meta.get("size"),
                    "path": str(p.rel_path) if p.rel_path and p.status in {"downloaded", "updated", "already_present"} else None,
                    "status": p.status,
                    "error": p.error,
                    **self._ref_dict(p.owner),
                    "also_referenced_in": [self._ref_dict(r) for r in p.also_in],
                }
                for p in planned
            ],
            "skipped_items": report.skipped_items,
        }
        tmp = course_dir / (MANIFEST_NAME + ".part")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        os.replace(tmp, course_dir / MANIFEST_NAME)

    # --------------------------------------------------------------- summary
    @staticmethod
    def _summary(course_id, course_name, course_dir, planned, report, dry_run) -> dict[str, Any]:
        status = Counter(p.status for p in planned)
        skipped_files = Counter(p.error for p in planned if p.status == "skipped")
        failed = [{"file_id": p.file_id, "path": str(p.rel_path), "error": p.error} for p in planned if p.status == "failed"]
        out: dict[str, Any] = {
            "course_id": int(course_id),
            "course_name": course_name,
            "destination": str(course_dir),
            "dry_run": dry_run,
            "modules_scanned": report.modules_scanned,
            "module_items_by_type": dict(report.item_types),
            "linked_pages_followed": report.pages_followed,
            "unique_files_referenced": len(planned),
            "files_by_status": dict(status),
            "files_skipped_by_reason": dict(skipped_files),
            "module_items_skipped": dict(Counter(s["reason"] for s in report.skipped_items)),
            "external_links_skipped": report.external_links_skipped,
            "failed": failed[:20],
        }
        if dry_run:
            out["planned_paths"] = [str(p.rel_path) for p in planned if p.rel_path][:100]
        else:
            out["manifest"] = str(course_dir / MANIFEST_NAME)
        return out
