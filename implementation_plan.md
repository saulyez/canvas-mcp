# Implementation Plan: Canvas MCP Server

## 🛠 Tech Stack
* **Language**: Python 3.12+
* **Package Manager**: `uv` (for lightning-fast dependency resolution and virtualenv management)
* **MCP Framework**: `FastMCP` (to rapidly build high-level, decorator-based tools)
* **HTTP Client**: `httpx` (asynchronous, robust, and supports HTTP/2)
* **Schema Parsing**: `PyYAML` (to parse the `apis.yml` for dynamic tool generation or validation)
* **Environment Management**: `python-dotenv` (for managing Canvas credentials and base URLs)

## 🏗 Project Structure
```text
canvas-mcp/
├── .env                # API Credentials (CLIENT_ID, CLIENT_SECRET, CANVAS_DOMAIN)
├── apis.yml            # The fetched OpenAPI schema
├── pyproject.toml      # uv-managed project metadata and dependencies
├── src/
│   ├── __init__.py
│   ├── server.py       # FastMCP server definition and tool entry points
│   ├── auth.py         # OAuth2 logic: token exchange, refreshing, and storage
│_  ├── client.py       # httpx wrapper for Canvas API calls (handles headers/pagination)
│   └── models.py       # Pydantic models derived from apis.yml
└── tests/              # Integration tests for API connectivity
```

## 🚀 Implementation Roadmap

### Phase 1: Project Initialization & Environment
* [ ] Initialize `uv` project in `canvas-mcp/`.
* [ ] Install core dependencies: `fastmcp`, `httpx`, `python-dotenv`, `pyyaml`, `pydantic`.
* [ ] Set up `.env` template with required Canvas variables.

### Phase 2: Authentication Engine (`auth.py`)
* [ ] Implement OAuth2 "Authorization Code" flow.
* [ ] Create a secure token storage mechanism (local encrypted file or OS keychain).
* [ ] Implement the **Automatic Refresh Logic**: Intercept `401 Unauthorized` responses to trigger token refresh using `refresh_token`.

### Phase 3: API Client Core (`client.py`)
* [ ] Build an `AsyncCanvasClient` class.
* [ ] Implement automated pagination logic (following `Link` headers).
* [ ] Integrate `httpx` with standard Bearer token injection.

### Phase 4: Tool Development (`server.py`)
* [ ] **Tool: `list_courses`** -> Mapping to `GET /api/v1/courses`.
* [ ] **Tool: `get_course_details`** -> Mapping to `GET /api/v1/courses/{id}`.
* [ ] **Tool: `list_assignments`** -> Mapping to `GET /api/v1/courses/{id}/assignments`.
* [ ] **Tool: `check_announcements`** -> Mapping to `GET /api/v1/courses/{id}/announcements`.

### Phase 5: Verification & Deployment
* [ ] Test tool execution with real Canvas API responses.
* [ ] Verify MCP inspector connectivity via `mcp dev`.
* [ ] Final cleanup and documentation of usage instructions.
