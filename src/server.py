from fastmcp import FastMCP
import os
from dotenv import load_dotenv
from src.auth import CanvasAuth
from src.client import AsyncCanvasClient

# Load environment variables
load_dotenv()

# Initialize MCP Server
mcp = FastMCP("Canvas")

# Initialize Auth and Client
auth = CanvasAuth()
client = AsyncCanvasClient(auth)

@mcp.tool()
async def list_courses() -> list[dict]:
    """Fetches a list of all active courses for the authenticated user."""
    try:
        return await client.get_paginated("GET", "courses")
    except Exception as e:
        return [{"error": str(e)}]

@mcp.tool()
async def get_course_details(course_id: str) -> dict:
    """Fetches detailed information for a specific course ID."""
    try:
        return await client.get_json(f"courses/{course_id}")
    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
async def list_assignments(course_id: str) -> list[dict]:
    """Fetches all assignments for a specific course ID."""
    try:
        return await client.get_paginated("GET", f"courses/{course_id}/assignments")
    except Exception as e:
        return [{"error": str(e)}]

@mcp.tool()
async def list_announcements(course_id: str) -> list[dict]:
    """Fetches all announcements for a specific course ID."""
    try:
        return await client.get_paginated("GET", f"courses/{course_id}/announcements")
    except Exception as e:
        return [{"error": str(e)}]

if __name__ == "__main__":
    mcp.run()
