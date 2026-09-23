# Implementation Plan: Course Material Downloader Tool

## 🎯 Objective
Create a specialized MCP tool that crawls Canvas **Modules**, identifies weekly content, and downloads all associated PDF files into a structured local directory hierarchy.

## 📂 Target Directory Structure
```text
<destination_root>/
└── <course_name>/
    ├── week_1/
    │   ├── material_a.pdf
    │_   └── material_b.pdf
    ├── week_2/
    │   └── ...
    └── ...
```

## 🔍 Technical Discovery (API Mapping)
To implement this, I must use the following Canvas API endpoints:
1.  **Get Course Info**: `GET /api/v1/courses/{course_id}` to retrieve the human-readable `name`.
2.  **List Modules**: `GET /api/v1/courses/{course_id}/modules` to identify the "weeks" (Modules act as our primary organizational unit).
3.  **Module Items**: `GET /api/v1/courses/{course_id}/modules/{module_id}/items` to find the files contained within each module.
4.  **File Download**: Extract the `url` from the file item and use `httpx` to stream the binary content.

## 🛠 Implementation Strategy

### Phase 1: The Downloader Engine (`src/downloader.py`)
I will create a new service class, `CanvasMaterialDownloader`, responsible for the heavy lifting.
*   **Logic**: It will iterate through modules $\rightarrow$ identify items of type `file` $\rightarrow$ resolve the download URL $\rightarrow$ execute the download.
*   _Dependency_: It will use the existing `AsyncCanvasClient` to perform API calls and respect the authentication state.

### Phase 2: The MCP Tool Definition (`src/server.py`)
I will add a new tool to the FastMCP server:
*   **Tool Name**: `download_course_materials`
*   **Arguments**:
    *   `course_id` (str): The Canvas ID of the course.
    *   `base_path` (str): The local directory where the download should start.
*   **Functionality**: It will trigger the engine and return a summary report (e.g., *"Downloaded 12 files across 4 weeks in 'Advanced AI' folder"*).

### Phase 3: Robustness & Edge Cases
*   **Naming Convention**: If a module doesn't have "Week" in its name, I will attempt to sanitize the name or use an incremental index (`week_1`, `week_2`).
*   **File Filtering**: The tool will explicitly filter for `.pdf` extensions as requested.
*   **Concurrency Control**: To avoid overwhelming Canvas (and getting 429 errors), downloads will be performed sequentially or with a limited semaphore.
*   **Collision Handling**: If a file with the same name exists, I will append a timestamp to prevent overwriting previous material.

## 🚀 Execution Roadmap
1.  [ ] **Research**: Verify module-to-file item mapping via `api_v1/modules`.
2.  [ ] **Development**: Implement `src/downloader.py`.
3.  [ ] **Integration**: Register the tool in `src/server.py`.
4.  [ ] **Testing**: Run a test download using a real course ID and verify the folder structure.
