from fastmcp import FastMCP
import os
import sys
import json
import logging
from dotenv import load_dotenv
from src.auth import CanvasAuth
from src.client import AsyncCanvasClient, CanvasAuthError
from src.downloader import CanvasMaterialDownloader

# Load environment variables
load_dotenv()

# stdout carries MCP JSON-RPC over stdio; all diagnostics go to stderr.
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# Initialize MCP Server
mcp = FastMCP("Canvas")

# Initialize Auth and Client
auth = CanvasAuth()
client = AsyncCanvasClient(auth)

@mcp.tool()
async def inspect_module_structure(course_id: str, module_id:	str) -> str:
    """
    A debug tool to return the keys and structure of a single module item.
    This helps identify where PDF links are hidden in the JSON response.
    """
    try:
        items = await client.get_paginated("GET", f"courses/{course_id}/modules/{module_id}/items")
        if not items:
            return "No items found in this module."
        
        # We only look at the first item to avoid massive output
        sample_item = items[0]
        
        # Create a simplified map of keys and types for debugging
        structure = {}
        for key, value in sample_item.items():
            if isinstance(value, (list, dict)):
                structure[key] = f"{type(value).__name__} (length: {len(value) if hasattr(value, '__len__') else 'N/A'})"
            else:
                structure[key] = str(type(value)).split('.')[-1]

        return json.dumps({"sample_item_keys": structure, "raw_sample_snippet": sample_item}, indent=2)
    except Exception as e:
        return f"Error inspecting module: {str(e)}"

@mcp.tool()
async def list_courses() -> list[dict]:
    """Fetches a list of all active courses for the authenticated user."""
    try:
        return await client.get_paginated("GET", "courses")
    except Exception as e:
        return [{"error": str(e)}]

@mcp.tool()
async def get_course_details(course_id: str) -> dict:
    """Fetches detailed information for a course ID."""
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

@mcp.tool()
async def download_course_materials(course_id: str, destination_root: str = "downloads", dry_run: bool = False) -> dict:
    """
    Downloads every file (any type) linked from the course's modules into
    <destination_root>/<course name>/<module name>/<file>.

    Files are discovered from File items and from file links inside module Pages,
    Assignments, Discussions (incl. attachments) and Quizzes, plus pages those link
    to (one hop). Inline images, external links/tools and locked items are skipped.
    Each file is stored once (first module wins); re-runs skip unchanged files.
    A manifest.json in the course folder records every file and its sources.

    Args:
        course_id: The Canvas ID of the course to download.
        destination_root: Base directory for downloads.
        dry_run: If true, report what would be downloaded without writing anything.
    """
    try:
        downloader = CanvasMaterialDownloader(client, os.path.abspath(destination_root))
        return await downloader.download_course_materials(course_id, dry_run=dry_run)
    except CanvasAuthError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

if __name__ == "__main__":
    mcp.run()
