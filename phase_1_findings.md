# Phase 1: Intelligence Gathering - Canvas API & OAuth2

## 🔐 Authentication (OAuth2)
Canvas uses standard OAuth2 (RFC-6749). For an MCP server, we need to handle the "Authorization Code" flow.

### Key Endpoints
*   **Authorize**: `GET https://<canvas-domain>/login/oauth2/auth`
    *   Parameters: `client_id`, `response_type=code`, `redirect_uri`, `state`, `scope`.
    *   Note: For native apps/local tools, `redirect_uri` can be `urn:ietf:wg:oauth:2.0:oob`.
*   **Token Exchange**: `POST https://<canvas-domain>/login/oauth2/token`
    *   Parameters: `grant_type` (`authorization_code` or `refresh_token`), `client_id`, `client_secret`.
*   **Refresh Token**: Use the `refresh_token` grant type to obtain new access tokens. *Crucial because tokens expire after 1 hour for newer developer keys.*

### Security Requirements
*   Tokens must be stored securely (e.g., OS keychain or encrypted local storage).
*   Never expose `client_secret`.

## 📚 Core Resource Discovery
The primary goal is to navigate courses and their contents.

### Courses (`GET /api/v1/courses`)
*   **Function**: Returns a paginated list of active courses for the authenticated user.
*   **Key Fields in Response**:
    *   `id`: Unique identifier (essential for all subsequent calls).
    *   `name`: Human-readable course name.
    *   `course_code`: The short identifier (e.g., `CS101`).
    *   `workflow_state`: Status (`available`, `unpublished`, etc.).

### Potential MCP Tools to Implement
Based on initial discovery, the following tools are high-priority:
1.  `list_courses`: Fetch all active courses for the user.
2.  `get_course_details`: Retrieve metadata (syllabus, dates) for a specific ID.
3.  `list_assignments`: (Targeting `/api/v1/courses/:id/assignments`) View upcoming deadlines.
4.  `get_announcements`: (Targeting `/api/v1/courses/:id/announcements`) Check for new class updates.

## 🛠 Technical Constraints & Patterns
*   **Pagination**: All list endpoints use pagination. The MCP server must handle following `Link` headers to ensure complete data retrieval.
*   **Throttling**: Must respect Canvas API rate limits (429 Too Many Requests) and implement backoff.
*   **Data Format**: Standard JSON.

## 🚀 Next Steps
1.  Define the specific Python environment and dependency list (`mcp`, `httpx`, `python-dotenv`).
2.  Design the local token storage schema.
3.  Implement the OAuth2 handshake logic (Phase 1 completion).
