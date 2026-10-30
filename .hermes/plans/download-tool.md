# Plan: download_course_materials (rewrite)

Status: IMPLEMENTED 2026-10-30 — 34/34 tests pass; live run on 72717 verified (87 files, 0 failed, idempotent).
Decision added: inline <img>-only files are skipped (Master, 2026-10-30).
Source of truth: Instructure Swagger (canvas.instructure.com/doc/api/{modules,files,pages}.json).
`apis.yml` is an api-evangelist APIs.json index only; its relative OpenAPI links 404.

## Decisions (Master, 2026-10-30)
1. File types: ANY type (no MIME filter).
2. External links (ExternalUrl / ExternalTool to non-Canvas hosts): skip, count in summary only.
3. Only files reachable from modules visible to the authenticated user. No Files-tab walk, no `_Unlinked/`.
   Assumption to confirm: files linked from a *page that a module page links to* (1 hop) count as part of
   that module — required for "flat template" courses (AI 2).
4. Phase 0 live probe: approved.
5. Plan saved here; stray `src/server.py\n` deleted (older broken copy).

## How Canvas attaches files to modules
Files live only in the course file store (folder tree). Modules hold ModuleItems that *reference* content:

| type        | where file ids are                                                   |
|-------------|----------------------------------------------------------------------|
| File        | `content_id`                                                         |
| Page        | `GET courses/{cid}/pages/{page_url}` -> `body` HTML                  |
| Assignment  | `GET courses/{cid}/assignments/{content_id}` -> `description` HTML   |
| Discussion  | `GET courses/{cid}/discussion_topics/{content_id}` -> `message` HTML + `attachments[]` |
| Quiz        | `GET courses/{cid}/quizzes/{content_id}` -> `description` HTML       |
| ExternalUrl | `external_url`; only a file if it is a Canvas `/files/N` link        |
| ExternalTool| none (LTI) — skip                                                    |
| SubHeader   | none — label only (optional subfolder name)                          |

Useful params: `modules?include[]=items&include[]=content_details` (items omitted for "too large"
modules -> fall back to `/modules/{id}/items`); `content_details.locked_for_user`.
File metadata: `GET courses/{cid}/files/{id}` -> `display_name`, `content-type`, `mime_class`, `size`,
`url` (= `/files/N/download?download_frd=1&verifier=...`, never ends in an extension), `locked_for_user`.

## Defects in current code
- downloader.py:61  `type == 'page'` — Canvas sends `"Page"`; page scraping never runs.
- downloader.py:117 `url.endswith('.pdf')` — always false; every file rejected (explains empty module dirs).
- downloader.py:96  `_is_pdf_link` true for any `/files/` URL — filters nothing.
- downloader.py:110 id = last path segment — yields `"download"` for `/files/N/download`.
- downloader.py:76,131,134 and auth.py:29 `print()` — corrupts MCP stdio JSON-RPC stream.
- downloader.py:79 no course-wide dedupe; silent overwrite on name collision.
- downloader.py:29,41 sanitisation misses `<>:"|?*` and control chars.
- downloader.py:124 unauthenticated download — depends on `verifier` being present.
- auth.py:31 direct token given fake 1-year expiry — real expiry surfaces only as a bare 401.

## Tasks

### 0. Live probe (read-only) — DONE 2026-10-30 (38 courses, 758 GETs, 250 s)
- Script: ~/.hermes/cache/scratch/canvas_probe/probe.py; report probe_report.json; page bodies in fixtures/.
- Item types: Page 535, SubHeader 236, File 227, ExternalUrl 150, ExternalTool 92, Discussion 38, Quiz 23, Assignment 12.
- File refs by source: Page.html 1781, Discussion.html 372, File items 227, Assignment.html 37,
  ExternalUrl->Canvas file 3, Quiz.html 0. => HTML scraping is the primary path, File items secondary.
- Files tab list: 403 in 37/38 courses => Files API listing unusable for students; confirms decision 3.
- Link shapes (all match the id regex): /api/v1/courses/N/files/N (data-api-endpoint), /courses/N/files/N?...,
  /courses/N/files/N/preview(?), /courses/N/files/N/download?, /api/v1/files/N, /files/N.
- Carriers: <a> (href + data-api-endpoint), <img> (src + data-api-endpoint), <iframe>. 915 unique ids via
  <a>/<iframe>, 412 ids appear ONLY as inline <img> (screenshots/diagrams/banners).
- 1-hop pages needed: AI 2 / SecNet / SEPP flat courses link to `module-name-2`; OS links `week-1-material`.
- AI 1 (72717) Week 1: all 7 known file ids found.
- File metadata samples (73): pdf 32, image 14, ppt 9, file 8, zip 5, doc 2, html 2, one 404 (dangling link).
  Download URL shape always /files/N/download?download_frd&verifier.
- Locked module items: 21 in MLF (70410), 6 in HCI (90239). No errors on Page/Assignment/Discussion/Quiz fetches.
- Courses with 0 modules (e.g. Libraries, EPS Wellbeing): tool must return cleanly.

### 1. HTML link extractor — `src/html_links.py`, `tests/test_html_links.py`
- stdlib `html.parser`; collect `href`, `src`, `data-api-endpoint`; `html.unescape`.
- file id regex `/(?:api/v1/)?(?:courses/\d+/)?files/(\d+)`; page regex `/courses/\d+/pages/<slug>`.
- Same-host only. Fixtures from probe (AI 1 Week 1 page, AI 2 flat page).

### 2. Resolver — `src/content_resolver.py`, `tests/test_content_resolver.py`
- Walk modules (include items + content_details, fallback endpoint).
- Dispatch per item type (table above); follow linked pages 1 hop, visited-set.
- Output `FileRef(file_id, module_id, module_name, module_position, subheader, source_type, source_title)`.
- Skip unpublished/locked_for_user items -> recorded with reason.

### 3. Downloader — `src/downloader.py` (rewrite)
- Global dedupe by file_id; first module (by position) owns the file, others noted in manifest.
- Metadata via `courses/{cid}/files/{id}`; skip `locked_for_user`.
- Stream with auth headers (httpx drops Authorization on cross-origin redirect to inst-fs).
- Write `.part` -> rename; skip if exists with same size; ` (1)` suffix on differing collision.
- Semaphore(4); retry 429 / 403 "Rate Limit Exceeded" with exponential backoff.
- Sanitise names: strip `<>:"/\|?*` + control chars, trim dots/spaces, cap 120 chars.
- `logging` to stderr only.

### 4. MCP tool — `src/server.py`; `src/auth.py`
- `download_course_materials(course_id, destination_root="downloads", dry_run=False)`.
- Writes `<course>/manifest.json`; returns summary: modules, items by type, files found / downloaded /
  skipped-by-reason, external links skipped.
- auth.py: remove print; clear error on `"Expired access token."` 401.

### 5. Verification
- pytest (extractor + resolver with mocked HTTP).
- Live: dry run then real run on course 72717; must include the 7 known Week 1 files
  (16149317-20, 16164838, 16170378, 15714471 jpg — now in scope since any type).
- Live: AI 2 flat course must yield files, not an empty dir.
