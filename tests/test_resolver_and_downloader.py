"""Resolver + downloader tests against an in-memory fake Canvas."""
import asyncio
import json

import httpx
import pytest

from src.content_resolver import ContentResolver, FileRef
from src.downloader import CanvasMaterialDownloader, module_folder_name, sanitize_name

HOST = "canvas.example.ac.uk"
API = f"https://{HOST}/api/v1"
CID = 1


def a(fid):  # linked file, real-world markup shape
    return (f'<a href="https://{HOST}/courses/{CID}/files/{fid}/download?download_frd=1&amp;verifier=v" '
            f'data-api-endpoint="{API}/courses/{CID}/files/{fid}">f{fid}</a>')


def page_link(slug):
    return f'<a href="/courses/{CID}/pages/{slug}">{slug}</a>'


def img(fid):
    return f'<img src="https://{HOST}/courses/{CID}/files/{fid}/preview">'


def http_error(path, status):
    req = httpx.Request("GET", f"{API}/{path}")
    return httpx.HTTPStatusError(str(status), request=req, response=httpx.Response(status, request=req))


class FakeAuth:
    base_url = f"https://{HOST}"

    async def get_auth_headers(self):
        return {"Authorization": "Bearer test"}


class FakeClient:
    def __init__(self, routes):
        self.routes = routes
        self.base_url = API
        self.auth = FakeAuth()
        self.calls = []

    def _get(self, path):
        self.calls.append(path)
        v = self.routes.get(path, 404)
        if isinstance(v, int):
            raise http_error(path, v)
        return v

    async def get_json(self, path, params=None):
        return self._get(path)

    async def get_paginated(self, method, path, params=None):
        return list(self._get(path))


def course_routes():
    m1_items = [
        {"id": 11, "position": 1, "type": "SubHeader", "title": "Lectures"},
        {"id": 12, "position": 2, "type": "Page", "title": "Week 1", "page_url": "week-1"},
        {"id": 13, "position": 3, "type": "File", "title": "Handout", "content_id": 103},
        {"id": 14, "position": 4, "type": "ExternalUrl", "title": "YT", "external_url": "https://youtube.com/x"},
        {"id": 15, "position": 5, "type": "ExternalTool", "title": "Panopto", "external_url": "https://panopto/x"},
        {"id": 16, "position": 6, "type": "Assignment", "title": "CW1", "content_id": 7},
        {"id": 17, "position": 7, "type": "Page", "title": "Locked", "page_url": "locked-page",
         "content_details": {"locked_for_user": True}},
    ]
    m2_items = [
        {"id": 21, "position": 1, "type": "Page", "title": "Week 2", "page_url": "week-2"},
        {"id": 22, "position": 2, "type": "Discussion", "title": "Q&A", "content_id": 9},
        {"id": 23, "position": 3, "type": "Quiz", "title": "Quiz", "content_id": 3},
        {"id": 24, "position": 4, "type": "ExternalUrl", "title": "Direct file",
         "external_url": f"https://{HOST}/courses/{CID}/files/204/download"},
    ]
    return {
        f"courses/{CID}": {"id": CID, "name": "AI 1 (34238, 34237)"},
        f"courses/{CID}/modules": [
            {"id": 2, "position": 2, "name": "Week 2 / Revision", "items": None},  # omitted -> fallback
            {"id": 1, "position": 1, "name": 'Week 1: "Intro"', "items": m1_items},
        ],
        f"courses/{CID}/modules/2/items": m2_items,
        f"courses/{CID}/pages/week-1": {
            "url": "week-1", "title": "Week 1",
            "body": a(101) + a(102) + img(900) + page_link("extra") + page_link("week-2"),
        },
        f"courses/{CID}/pages/week-2": {"url": "week-2", "title": "Week 2", "body": a(201) + a(101)},
        f"courses/{CID}/pages/extra": {
            "url": "extra", "title": "Extra reading", "body": a(301) + a(103) + page_link("extra2"),
        },
        f"courses/{CID}/pages/extra2": {"url": "extra2", "title": "Too deep", "body": a(999)},
        f"courses/{CID}/assignments/7": {"description": a(104)},
        f"courses/{CID}/discussion_topics/9": {"message": a(202), "attachments": [{"id": 203}]},
        f"courses/{CID}/quizzes/3": {"description": ""},
    }


# --------------------------------------------------------------------- resolver
def resolve(routes=None):
    client = FakeClient(routes or course_routes())
    return asyncio.run(ContentResolver(client, CID).resolve()), client


def test_resolver_finds_all_module_files():
    rep, _ = resolve()
    assert {r.file_id for r in rep.refs} == {101, 102, 103, 104, 201, 202, 203, 204, 301}


def test_resolver_skips_inline_images_and_depth_two():
    rep, client = resolve()
    ids = {r.file_id for r in rep.refs}
    assert 900 not in ids and 999 not in ids
    assert f"courses/{CID}/pages/extra2" not in client.calls


def test_resolver_does_not_follow_into_module_pages():
    rep, client = resolve()
    assert client.calls.count(f"courses/{CID}/pages/week-2") == 1
    owner_201 = [r for r in rep.refs if r.file_id == 201]
    assert [r.module_id for r in owner_201] == [2]


def test_resolver_counts_and_skips():
    rep, _ = resolve()
    assert rep.modules_scanned == 2
    assert rep.external_links_skipped == 2
    assert rep.pages_followed == 1
    assert [s["reason"] for s in rep.skipped_items] == ["locked"]
    assert rep.item_types["Page"] == 3


def test_resolver_records_subheader_and_via_link():
    rep, _ = resolve()
    r301 = next(r for r in rep.refs if r.file_id == 301)
    assert r301.via_link and r301.source_type == "LinkedPage" and r301.subheader == "Lectures"


def test_resolver_empty_course():
    rep, _ = resolve({f"courses/{CID}/modules": []})
    assert rep.refs == [] and rep.modules_scanned == 0


def test_resolver_page_error_is_skipped_not_fatal():
    routes = course_routes()
    routes[f"courses/{CID}/pages/week-1"] = 403
    rep, _ = resolve(routes)
    assert {"page unavailable"} <= {s["reason"] for s in rep.skipped_items}
    assert 103 in {r.file_id for r in rep.refs}


# ------------------------------------------------------------------- downloader
def test_plan_ownership_prefers_direct_reference():
    mk = lambda fid, mid, pos, via: FileRef(fid, mid, f"m{mid}", mid, pos, "Page", "t", None, via)
    planned = CanvasMaterialDownloader._plan([mk(5, 1, 1, True), mk(5, 2, 1, False), mk(5, 3, 1, False)])
    assert len(planned) == 1
    assert planned[0].owner.module_id == 2
    assert [r.module_id for r in planned[0].also_in] == [1, 3]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('Week 11: April 22nd, 2024 (Revision)', "Week 11_ April 22nd, 2024 (Revision)"),
        ('<Module_Name> - "flat" template.', "Module_Name_ - _flat_ template"),
        ("../../etc/passwd", "etc_passwd"),
        ("   ", "fallback"),
        ("CON.txt", "_CON.txt"),
        ("a&amp;b.pdf", "a&b.pdf"),
    ],
)
def test_sanitize_name(raw, expected):
    assert sanitize_name(raw, "fallback") == expected


def test_sanitize_truncates_keeping_extension():
    s = sanitize_name("x" * 300 + ".pptx", "f")
    assert len(s) <= 120 and s.endswith(".pptx")


CONTENT = {fid: f"content-{fid}".encode() * (fid % 7 + 1) for fid in (101, 102, 103, 104, 201, 202, 203, 204, 301)}


def file_meta(fid, **over):
    m = {"id": fid, "display_name": f"file{fid}.pdf", "content-type": "application/pdf",
         "size": len(CONTENT[fid]), "url": f"https://{HOST}/files/{fid}/download?download_frd=1&verifier=v",
         "locked_for_user": False}
    m.update(over)
    return m


def download_routes():
    r = course_routes()
    for fid in CONTENT:
        r[f"courses/{CID}/files/{fid}"] = file_meta(fid)
    r[f"courses/{CID}/files/102"] = file_meta(102, display_name="file101.pdf")  # same name as 101
    r[f"courses/{CID}/files/104"] = file_meta(104, locked_for_user=True)
    r[f"courses/{CID}/files/203"] = 404  # cross-course attachment -> global fallback
    r["files/203"] = file_meta(203)
    r[f"courses/{CID}/files/204"] = 404
    r["files/204"] = 404
    return r


def transport(seen):
    def handler(request: httpx.Request):
        seen.append(request)
        fid = int(request.url.path.split("/")[2])
        return httpx.Response(200, content=CONTENT[fid])
    return httpx.MockTransport(handler)


def run(tmp_path, routes, dry_run=False, seen=None):
    seen = [] if seen is None else seen
    dl = CanvasMaterialDownloader(FakeClient(routes), tmp_path, transport=transport(seen))
    return asyncio.run(dl.download_course_materials(CID, dry_run=dry_run)), seen


def test_download_end_to_end(tmp_path):
    summary, seen = run(tmp_path, download_routes())
    course = tmp_path / "AI 1 (34238, 34237)"
    w1, w2 = course / "week_1", course / "week_2"
    assert (w1 / "file101.pdf").read_bytes() == CONTENT[101]
    assert (w1 / "file101 (1).pdf").read_bytes() == CONTENT[102]  # name collision
    assert (w1 / "file103.pdf").exists() and (w1 / "file301.pdf").exists()
    assert (w2 / "file201.pdf").exists() and (w2 / "file203.pdf").exists()
    assert not (w2 / "file101.pdf").exists()  # stored once, in owner module
    assert summary["files_by_status"] == {"downloaded": 7, "skipped": 2}
    assert summary["files_skipped_by_reason"] == {"locked": 1, "metadata http 404": 1}
    assert all(r.headers["Authorization"] == "Bearer test" for r in seen)
    assert not list(tmp_path.rglob("*.part"))

    manifest = json.loads((course / "manifest.json").read_text())
    m101 = next(f for f in manifest["files"] if f["file_id"] == 101)
    assert m101["path"] == "week_1/file101.pdf"
    assert [x["module"] for x in m101["also_referenced_in"]] == ["Week 2 / Revision"]


def test_rerun_is_idempotent(tmp_path):
    run(tmp_path, download_routes())
    summary, seen = run(tmp_path, download_routes())
    assert summary["files_by_status"] == {"already_present": 7, "skipped": 2}
    assert seen == []


def test_rerun_keeps_names_and_updates_changed_file(tmp_path):
    run(tmp_path, download_routes())
    CONTENT[102] = b"new version of 102, longer than before"
    try:
        routes = download_routes()
        summary, _ = run(tmp_path, routes)
        p = tmp_path / "AI 1 (34238, 34237)" / "week_1" / "file101 (1).pdf"
        assert p.read_bytes() == CONTENT[102]
        assert summary["files_by_status"]["updated"] == 1
    finally:
        CONTENT[102] = b"content-102" * (102 % 7 + 1)


def test_dry_run_writes_nothing(tmp_path):
    summary, seen = run(tmp_path, download_routes(), dry_run=True)
    assert list(tmp_path.iterdir()) == [] and seen == []
    assert summary["files_by_status"]["would_download"] == 7
    assert "week_1/file101.pdf" in summary["planned_paths"]


def test_size_mismatch_fails_cleanly(tmp_path):
    routes = download_routes()
    routes[f"courses/{CID}/files/101"] = file_meta(101, size=999999)
    summary, _ = run(tmp_path, routes)
    assert summary["files_by_status"]["failed"] == 1
    assert "size mismatch" in summary["failed"][0]["error"]
    assert not list(tmp_path.rglob("*.part"))


# ---------------------------------------------------------------- folder naming
# Real module names from the user's courses (Phase 0 probe).
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Week 1: January 15th, 2024", "week_1"),
        ("Week 11: April 22nd, 2024 (Revision)", "week_11"),
        ("Week 01: September 30th, 2024", "week_1"),
        ("Week 1 - Sept 28th 2026 - Introduction, Perceptron, Optimisation", "week_1"),
        ("Week 6  Reading Week", "week_6"),
        ("Week 7 - supporting materials - Understanding the things in the Internet of Things", "week_7"),
        ("Semester 2   Week 1", "s2_week_1"),
        ("S2 Week 6  Reading Week ", "s2_week_6"),
        ("Semester 1 Week 3", "week_3"),
        ("Week 1-4", "week_1-4"),
        ("Weeks 12 to 16: Assessment Support and Final Assessments", "week_12-16"),
        ("Week 6 - 2024 recap", "week_6"),  # not a range
        ("About your module", "about_your_module"),
        ("Exam Feedback and Resit", "exam_feedback_and_resit"),
        ("Topic 01: Introduction, Arrays and Linked Lists", "topic_01_introduction_arrays_and_linked_lists"),
        ("Consolidation Week", "consolidation_week"),
        ("Revision Week April 2025: Mock exam", "revision_week_april_2025_mock_exam"),
        ("1. Welcome & Settling In", "1_welcome_settling_in"),
        ("Step 1 - Discover", "step_1_discover"),
        ('<Module_Name> - This module is a "flat" template that can be used if you prefer using a single '
         'Canvas page for your module.',
         "module_name_this_module_is_a_flat_template_that_can_be_used_if_you_prefer_using_a_single_canvas_page_for_your_module"),
        ("", "fallback"),
    ],
)
def test_module_folder_name(raw, expected):
    assert module_folder_name(raw, "fallback") == expected


def test_same_week_modules_merge_into_one_folder(tmp_path):
    routes = download_routes()
    for m in routes[f"courses/{CID}/modules"]:
        if m["id"] == 2:
            m["name"] = "Week 1 - supporting materials"
    summary, _ = run(tmp_path, routes)
    course = tmp_path / "AI 1 (34238, 34237)"
    assert sorted(d.name for d in course.iterdir() if d.is_dir()) == ["week_1"]
    assert (course / "week_1" / "file201.pdf").exists()
    assert summary["files_by_status"]["downloaded"] == 7


def test_rename_migrates_existing_files_without_downloading(tmp_path):
    run(tmp_path, download_routes())
    course = tmp_path / "AI 1 (34238, 34237)"
    # Simulate a download made with the old folder naming.
    old = course / "Week 1_ _Intro"
    (course / "week_1").rename(old)
    mpath = course / "manifest.json"
    manifest = json.loads(mpath.read_text())
    for f in manifest["files"]:
        if f["path"] and f["path"].startswith("week_1/"):
            f["path"] = f["path"].replace("week_1/", "Week 1_ _Intro/", 1)
    mpath.write_text(json.dumps(manifest))

    # week_1 holds 101, 102 ("file101 (1).pdf"), 103, 301; week_2 holds 201, 202, 203.
    dry, seen = run(tmp_path, download_routes(), dry_run=True)
    assert dry["files_by_status"].get("would_move") == 4 and seen == []

    summary, seen = run(tmp_path, download_routes())
    assert summary["files_by_status"] == {"moved": 4, "already_present": 3, "skipped": 2}
    assert seen == []  # nothing re-downloaded
    assert not old.exists()  # emptied old folder removed
    assert (course / "week_1" / "file101 (1).pdf").read_bytes() == CONTENT[102]


def test_old_folder_with_foreign_files_is_kept(tmp_path):
    run(tmp_path, download_routes())
    course = tmp_path / "AI 1 (34238, 34237)"
    old = course / "Old Week 1"
    (course / "week_1").rename(old)
    (old / "my_notes.txt").write_text("mine")
    mpath = course / "manifest.json"
    manifest = json.loads(mpath.read_text())
    for f in manifest["files"]:
        if f["path"] and f["path"].startswith("week_1/"):
            f["path"] = f["path"].replace("week_1/", "Old Week 1/", 1)
    mpath.write_text(json.dumps(manifest))
    run(tmp_path, download_routes())
    assert (old / "my_notes.txt").read_text() == "mine"
    assert [p.name for p in old.iterdir()] == ["my_notes.txt"]
