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

# Tier 1 (overview): a narrative paragraph plus a short label per moment. Cheap
# to generate — labels add ~no time because the cost is in volume of generated
# text, and the structured per-event detail (which doubled latency) is gone.
OVERVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "narrative": {
            "type": "string",
            "description": "2-4 sentence provenance story: where the text first appeared, how it moved between apps/conversations, and where it ended up.",
        },
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "anchor_index": {"type": "integer", "description": "Index into the provided anchor list this label describes."},
                    "label": {"type": "string", "description": "Title of ≤8 words for what happened at this moment."},
                },
                "required": ["anchor_index", "label"],
            },
        },
    },
    "required": ["narrative", "moments"],
}

# Tier 2 (zoom): one focused paragraph about a single moment.
MOMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "detail": {
            "type": "string",
            "description": "A focused paragraph on this single moment: what was happening, who was involved, and the substance — drawing on the audio transcript where present.",
        },
    },
    "required": ["detail"],
}


def _strip_pua(text):
    """Drop Unicode private-use glyphs (app icon fonts OCR into noise)."""
    return "".join(c for c in text if not (0xE000 <= ord(c) <= 0xF8FF))


def _format_frames(events):
    """Render normalized frames as numbered SCREEN/AUDIO context lines."""
    lines = []
    for i, e in enumerate(events):
        text = _strip_pua(e.get("text") or "")
        text = " ".join(text.split())[:MAX_FRAME_CHARS]
        if e.get("is_audio"):
            spk = e.get("speaker") or "unknown speaker"
            lines.append(f"[frame {i}] AUDIO {e.get('timestamp', '?')} | speaker={spk}\n{text}")
        else:
            lines.append(
                f"[frame {i}] SCREEN {e.get('timestamp', '?')} | app={e.get('app_name', '?')} "
                f"| window={e.get('window_name', '')}\n{text}"
            )
    return "\n".join(lines)


_CONTEXT_PREAMBLE = (
    "Below is captured context: SCREEN frames (noisy OCR of what was visible) and "
    "AUDIO transcripts (rough speech-to-text of what was said nearby). The term "
    "itself may not appear in every item. The audio transcription is low-quality, "
    "so infer meaning rather than quoting it verbatim."
)


def _call_gemini(prompt, schema, api_key):
    """POST a prompt + response schema to Gemini, return the parsed JSON object."""
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema},
    }
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.load(resp)
    return json.loads(body["candidates"][0]["content"]["parts"][0]["text"])


def analyze_overview(query_text, frames, anchors, api_key):
    """Tier 1: narrative paragraph + a short label per anchor moment.

    `anchors` is the list of anchor timestamps; the returned `moments` carry an
    `anchor_index` back into it. Fast — no structured per-event generation.
    """
    anchor_list = "\n".join(f"[anchor {i}] {ts}" for i, ts in enumerate(anchors))
    prompt = (
        f'A user searched for: "{query_text}"\n\n{_CONTEXT_PREAMBLE}\n\n'
        "Reconstruct what was happening around this term: where it came from, what "
        "the user was doing, and any meeting or conversation it relates to (topic, "
        "and who was involved if discernible). Write a short narrative paragraph. "
        f"Then give a ≤8-word label for each anchor moment below.\n\n"
        f"DISTINCT MOMENTS:\n{anchor_list}\n\nCONTEXT ITEMS:\n{_format_frames(frames)}"
    )
    return _call_gemini(prompt, OVERVIEW_SCHEMA, api_key)


def analyze_moment(query_text, frames, api_key):
    """Tier 2: a focused paragraph about a single zoomed-in moment.

    `frames` should be just that one moment's context (a tight window), so the
    prompt is small and focused — faster and more accurate than the broad pass.
    """
    prompt = (
        f'A user is examining one moment related to: "{query_text}"\n\n{_CONTEXT_PREAMBLE}\n\n'
        "Describe in one focused paragraph what was happening at this specific "
        "moment: who was involved and the substance of any conversation, leaning on "
        f"the audio transcript where present.\n\nCONTEXT ITEMS:\n{_format_frames(frames)}"
    )
    return _call_gemini(prompt, MOMENT_SCHEMA, api_key)


def main():
    """Standalone: read frames JSON on stdin, print the overview (no anchors)."""
    if len(sys.argv) < 2:
        sys.exit("Usage: ... | python3 analyze.py \"query text\"")
    frames = json.load(sys.stdin)
    api_key = config.require_key("GEMINI_API_KEY")
    anchors = sorted({f["timestamp"] for f in frames if f.get("timestamp")})
    try:
        result = analyze_overview(sys.argv[1], frames, anchors, api_key)
    except urllib.error.HTTPError as e:
        sys.exit(f"Gemini API error {e.code}: {e.read().decode(errors='replace')[:300]}")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach Gemini: {e.reason}")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
