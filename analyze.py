#!/usr/bin/env python3
"""Context Trace — provenance analysis via Gemini.

Turns raw Screenpipe frames (low-signal, redundant OCR dumps) into a provenance
narrative: a top-line story of where the text came from and how it moved, plus a
deduplicated list of the distinct events along that journey.

Each returned event carries `frame_index` pointing back to the original frame so
the UI can show that frame's screenshot as supporting evidence.

Stdlib only (urllib) — no google-generativeai dependency. Uses the Gemini REST
API with structured-JSON output.

Usage (normally called by render.py, but runnable standalone):
    python3 query.py "term" --json | python3 analyze.py "term"
"""

import json
import sys
import urllib.error
import urllib.request

import config

MODEL = "gemini-3.5-flash"
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

# Each frame's OCR text is the whole screen; trim so the prompt stays focused
# and cheap while keeping enough context around the match for reasoning.
MAX_FRAME_CHARS = 1500

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "narrative": {
            "type": "string",
            "description": "2-4 sentence provenance story: where the text first appeared, how it moved between apps, and where it ended up.",
        },
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "One plain-English line describing what happened at this step."},
                    "app": {"type": "string"},
                    "timestamp": {"type": "string"},
                    "frame_index": {"type": "integer", "description": "Index into the provided frames list this event corresponds to."},
                },
                "required": ["summary", "frame_index"],
            },
        },
    },
    "required": ["narrative", "events"],
}


def _strip_pua(text):
    """Drop Unicode private-use glyphs (app icon fonts OCR into noise)."""
    return "".join(c for c in text if not (0xE000 <= ord(c) <= 0xF8FF))


def build_prompt(query_text, events):
    """Compose the prompt: the traced text plus the numbered raw frames."""
    lines = [
        f'A user searched for: "{query_text}"',
        "",
        "Below is captured context from the moments when this term was on screen: "
        "both SCREEN frames (noisy OCR of what was visible) and AUDIO transcripts "
        "(rough speech-to-text of what was being said nearby). The term itself may "
        "not appear in every item — these are the surrounding context of each moment.",
        "",
        "Reconstruct what was happening around this term: where it came from, what "
        "the user was doing, and — using the AUDIO transcripts — any meeting or "
        "conversation it relates to (topic, and who was involved if discernible). "
        "Collapse near-duplicates into distinct events. For each event give a "
        "one-line plain-English summary and the frame_index it best corresponds to. "
        "Then write a short overall narrative. Note the audio transcription is "
        "low-quality, so infer meaning rather than quoting it verbatim.",
        "",
        "CONTEXT ITEMS:",
    ]
    for i, e in enumerate(events):
        text = _strip_pua(e.get("text") or "")
        text = " ".join(text.split())[:MAX_FRAME_CHARS]
        if e.get("is_audio"):
            spk = e.get("speaker") or "unknown speaker"
            lines.append(
                f"[frame {i}] AUDIO {e.get('timestamp', '?')} | speaker={spk}\n{text}"
            )
        else:
            lines.append(
                f"[frame {i}] SCREEN {e.get('timestamp', '?')} | app={e.get('app_name', '?')} "
                f"| window={e.get('window_name', '')}\n{text}"
            )
    return "\n".join(lines)


def analyze(query_text, events, api_key):
    """Call Gemini and return {narrative, events:[...]}. Raises on failure."""
    payload = {
        "contents": [{"parts": [{"text": build_prompt(query_text, events)}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.load(resp)
    text = body["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: ... | python3 analyze.py \"query text\"")
    query_text = sys.argv[1]
    events = json.load(sys.stdin)
    api_key = config.require_key("GEMINI_API_KEY")
    try:
        result = analyze(query_text, events, api_key)
    except urllib.error.HTTPError as e:
        sys.exit(f"Gemini API error {e.code}: {e.read().decode(errors='replace')[:300]}")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach Gemini: {e.reason}")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
