<!--
ARCHIVED ORIGINAL BRIEF — SUPERSEDED BY CLAUDE.md.
Kept for the original intent and user-flow narrative. Several specifics here are
now known to be wrong: it omits API auth, describes the wrong /search response
schema, and proposes UI stacks (Tkinter/PyQt/webview) we've since rejected in
favor of generated HTML opened in Chrome. Trust CLAUDE.md where they disagree.
-->

# Project Specification: "Context Trace" (macOS Data Lineage Service)

## 1. Project Objective
Build a macOS Service (Quick Action) that allows a user to highlight any text, right-click to select "Show History," and instantly view the chronological data lineage of that text (e.g., where it was previously seen, copied, or typed). 

The goal is to provide a "Data Lifeline" that tracks the provenance of information across applications using **Screenpipe** as the underlying local data engine.

## 2. Core Architecture
The system relies on a decoupled, local-first architecture:
1. **The Backend (Screenpipe):** Runs continuously in the background. It captures event-driven screen data (macOS Accessibility Tree, OCR, and Clipboard events) and stores them in a local SQLite database, exposed via a local REST API.
2. **The Trigger (macOS Shortcuts/Services):** Captures the user's highlighted text string and passes it to the processing script.
3. **The Processor (Python/Node.js Script):** Takes the text string, formats an HTTP request to the Screenpipe API, and parses the returned JSON.
4. **The UI (Micro-GUI):** A lightweight, native-feeling window that displays the chronological history of the text, including the application name, timestamp, and visual context (the screenshot).

## 3. User Flow
1. User highlights text in any macOS application (e.g., a hotel address in a Word doc: `123 Main St, London`).
2. User right-clicks and selects `Services -> Show History`.
3. A small, native UI window pops up.
4. The window displays a chronological list of when and where that text appeared previously (e.g., `10:00 AM - Outlook`, `10:05 AM - System Clipboard`, `10:15 AM - Pages`).
5. Clicking on a history item displays the screenshot frame from that exact moment to provide visual narrative context (e.g., the user sees the actual email the text was copied from).

## 4. Screenpipe Backend Requirements
- **Prerequisite:** Screenpipe must be installed and running on the Mac.
- **Local API URL:** `http://localhost:3030`
- **Primary Endpoint:** `GET /search`
- **Query Parameters for Script:**
  - `q`: The highlighted text string (URL encoded).
  - `content_type`: Set to `accessibility` (best for precise text matching) or `all`.
  - `limit`: Set a reasonable limit (e.g., 10) to avoid overloading the UI.
- **Example API Call:**
  `curl "http://localhost:3030/search?q=123+Main+St&content_type=accessibility&limit=10"`

## 5. Development Steps for the AI Agent
Please execute the following build phases:

### Phase 1: The Query Module
- Write a script (Python or Node.js) that accepts a string argument from the command line.
- Execute a GET request to the local Screenpipe API (`http://localhost:3030/search`) using the string.
- Parse the JSON response to extract: `timestamp`, `app_name`, `window_name`, the surrounding text context, and the associated screenshot path or frame ID.

### Phase 2: The Micro-GUI
- Build a lightweight UI (using Python `Tkinter`/`PyQt`, or a simple web-view approach) to display the parsed JSON data.
- The UI should present a chronological timeline or list.
- **Crucial:** The UI must be able to load and display the local screenshot images associated with the timeline events so the user has visual context.

### Phase 3: The macOS Integration
- Provide the exact instructions or script needed to wrap the Phase 1 & 2 executable into a macOS Shortcut (Quick Action).
- The Shortcut must be configured to accept "Text" from "Any Application" and pass that text as the standard input to the script.

## 6. Known Constraints & Fallbacks
- **Deep Linking Limitation:** Attempting to use AppleScript to deep-link the user back into the exact paragraph of an external application is highly unstable. **Fallback:** Rely on displaying the Screenpipe screenshot. The visual context of the screenshot is sufficient for the MVP.
- **Performance:** Keep the UI lightweight so it feels instantaneous. Do not load all screenshots into memory at once; load them lazily when the user selects a specific timeline item.