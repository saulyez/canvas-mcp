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
| `list_announcements` | Scans and retrieves recent announcements published to a or course. |

## 🏗 Project Structure
- `src/auth.py`: Handles token lifecycle, refreshing, and secure storage.
- `src/client.py`: The asynchronous API engine with intelligent pagination support.
- `src/server.py`: The FastMCP entry point defining the toolset.
- `.vscode/mcp.json`: Pre-configured VS Code integration settings.
