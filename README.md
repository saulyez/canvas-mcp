# Canvas MCP Server

A high-performance Model Context Protocol (MCP) server designed to allow AI agents (like Claude Desktop or VS Code extensions) to interact seamlessly with your Canvas LMS instance.

## 🚀 Features

- **Seamless Authentication**: Supports both full OAuth2 flows and a "Direct Token" mode for instant setup using personal access tokens.
- **Auto-Pagination**: Automatically traverses all pagination links in the Canvas API, ensuring you never miss a course or assignment due to truncated lists.
- **Deep Inspection**: Retrieve detailed metadata, syllabus information, and structured course content.

## 🛠 Setup & Installation

### Prerequisites
- [uv](https://github.com/astral-sh/uv) installed on your system.
- A valid Canvas Access Token or OAuth2 credentials.

### Configuration
Create a `.env` file in the project root:

```bash
# The hostname of your Canvas instance (e.g., university.instructure.com)
CANVAS_DOMAIN=your-university.instructure.com

# Option A: Use a direct personal access token (Easiest)
CANVAS_ACCESS_TOKEN=your_token_here

# Option B: Or provide OAuth2 credentials for full flow
CLIENT_ID=your_client_id
CLIENT_SECRET=your_client_secret
REDIRECT_URI=http://localhost:8080/callback
```

### Running the Server
To start the server locally, run:

```bash
uv run python -m src.server
```

## 🛠 Available Tools

| Tool | Description |
| :--- | :--- |
| `list_courses` | Fetches a complete list of all active courses for the authenticated user. |
| `get_course_details` | Retrieves in-depth metadata, including syllabus and status, for a specific course ID. |
| `list_assignments` | Lists all assignments and upcoming deadlines within a specific course. |
| `list_announcements` | Scans and retrieves recent announcements published to a course. |
| `download_course_materials` | Downloads every file reachable from a course's modules into a local folder tree, one folder per module/week. Supports a `dry_run` preview. See below. |
| `inspect_module_structure` | Debug tool: returns the keys and raw JSON of the first item in a module, to see how Canvas references content. |

### `download_course_materials`

**Arguments**

| Argument | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `course_id` | `str` | required | Canvas course ID (from `list_courses`). |
| `destination_root` | `str` | `downloads` | Base directory. Relative paths resolve against the server's working directory. |
| `dry_run` | `bool` | `false` | Report what would be downloaded, moved, or updated without writing anything. |

**Output layout**

```text
<destination_root>/
└── <course name>/
    ├── manifest.json
    ├── week_1/
    │   └── W1.1 Intro.pdf
    ├── week_2/
    ├── week_12-16/        # "Weeks 12 to 16"
    ├── s2_week_1/         # "Semester 2 Week 1"
    └── assessment_info/   # non-week modules -> snake_case
```

Modules that map to the same folder name (e.g. "Week 7" and "Week 7 - supporting materials") are merged into one folder.

**How files are discovered**

Canvas does not store files *in* modules. Module items reference content, and files hang off that content:

| Module item type | Where the files come from |
| :--- | :--- |
| `File` | The item's `content_id` is the file ID. |
| `Page` | File links in the page body HTML. |
| `Assignment` | File links in the description HTML. |
| `Discussion` | File links in the message HTML, plus topic attachments. |
| `Quiz` | File links in the description HTML. |
| `ExternalUrl` | Only when it points at a file on the same Canvas host. |
| `ExternalTool`, `SubHeader` | Skipped (never carry files). |

Pages linked from module content that are not module items themselves are followed one hop. Inline `<img>` embeds, external links/tools, and locked files are skipped.

**Behaviour**

- **All file types** are downloaded (PDF, slides, archives, etc.), not only PDFs.
- **Deduplication**: a file referenced from several modules is stored once. A direct module reference beats a followed link, then the earliest module wins. Every other reference is recorded in `manifest.json` under `also_referenced_in`.
- **Re-runs are incremental**: files already present with the expected size are skipped. If folder naming changes between runs, existing files are moved rather than re-downloaded.
- **Safe writes**: downloads stream to a `.part` file and are size-checked before an atomic rename. Filenames are sanitised and path traversal outside `destination_root` is rejected. Name collisions get a ` (1)` suffix.
- **Rate limiting**: requests run with bounded concurrency (4) and back off on Canvas `429`/throttling responses.

**Return value** — a JSON summary including `modules_scanned`, `module_items_by_type`, `linked_pages_followed`, `unique_files_referenced`, `files_by_status` (`downloaded`, `updated`, `moved`, `already_present`, `skipped`, `failed`), `files_skipped_by_reason`, the first 20 failures, and the path to `manifest.json` (or `planned_paths` on a dry run).

**Example**

Run a dry run first, then the real download:

```text
download_course_materials(course_id="72717", dry_run=true)
download_course_materials(course_id="72717", destination_root="/home/saul/Documents/uni")
```

## 🏗 Project Structure
- `src/auth.py`: Handles token lifecycle, refreshing, and secure storage.
- `src/client.py`: The asynchronous API engine with intelligent pagination support.
- `src/server.py`: The FastMCP entry point defining the toolset.
- `src/content_resolver.py`: Walks a course's modules and collects every reachable Canvas file reference.
- `src/html_links.py`: Extracts Canvas file and page links from Canvas-authored HTML (ignores inline images).
- `src/downloader.py`: Plans folder layout, deduplicates, downloads, and writes `manifest.json`.
- `tests/`: Pytest suite for link extraction, resolution, and downloading (`uv run pytest`).
- `.vscode/mcp.json`: Pre-configured VS Code integration settings.
