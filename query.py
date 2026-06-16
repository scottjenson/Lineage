#!/usr/bin/env python3
"""Context Trace — Phase 1 query module.

Takes a text string, queries the local Screenpipe /search API, and prints the
chronological data lineage of that text (when/where it was seen, with the
screenshot path for each moment).

Stdlib only — no pip install required.

Usage:
    python3 query.py "123 Main St"
    python3 query.py "123 Main St" --json
    python3 query.py "123 Main St" --content-type all --limit 20
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import config

API_BASE = "http://localhost:3030"


def resolve_api_key():
    """Find the Screenpipe API key via the shared resolver (env → config.json)."""
    return config.get_key("SCREENPIPE_API_KEY")


def doctor():
    """Check that the runtime environment can actually reach Screenpipe.

    The macOS Service runs this script in a stripped-down environment (no
    ~/.zshrc, minimal PATH). This prints a clear pass/fail diagnostic for each
    prerequisite so a failed demo points at the real cause instead of a silent
    blank window. Returns True if everything passed.
    """
    ok = True

    print(f"• python3: {sys.executable}")

    key = resolve_api_key()
    if key:
        print(f"• API key: found ({key[:6]}…)")
    else:
        print("• API key: NOT FOUND — set $SCREENPIPE_API_KEY, or add it to "
              "config.json next to the scripts.")
        ok = False

    if key:
        try:
            search("ping", key, limit=1)
            print(f"• Screenpipe: reachable at {API_BASE}")
        except urllib.error.HTTPError as e:
            print(f"• Screenpipe: HTTP {e.code} ({e.reason}) — "
                  f"{'bad API key?' if e.code in (401, 403) else 'unexpected'}")
            ok = False
        except urllib.error.URLError as e:
            print(f"• Screenpipe: UNREACHABLE at {API_BASE} — is it running? ({e.reason})")
            ok = False

    print("OK" if ok else "FAILED")
    return ok


def search(query, api_key, content_type="ocr", limit=10, start_time=None, end_time=None):
    """Call GET /search and return the list of raw result dicts.

    `start_time`/`end_time` are ISO-8601 strings bounding the time window.
    """
    q = {"q": query, "content_type": content_type, "limit": limit}
    if start_time:
        q["start_time"] = start_time
    if end_time:
        q["end_time"] = end_time
    req = urllib.request.Request(
        f"{API_BASE}/search?{urllib.parse.urlencode(q)}",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp).get("data", [])


def search_window(query, api_key, days=7, per_day=8, content_type="ocr"):
    """Sample up to `per_day` frames from each of the last `days` days.

    Screenpipe returns newest-first, so a single limit-capped call buries older
    appearances under a busy recent day. Sampling per-day guarantees each day is
    represented, so an important older event survives into the analysis.
    Days with no matches contribute nothing. Returns raw result dicts.
    """
    results = []
    today = datetime.now(timezone.utc).date()
    for d in range(days):
        day = today - timedelta(days=d)
        start = f"{day.isoformat()}T00:00:00Z"
        end = f"{day.isoformat()}T23:59:59Z"
        try:
            results.extend(
                search(query, api_key, content_type, per_day, start, end)
            )
        except urllib.error.URLError:
            continue  # skip a day that errors rather than failing the whole search
    return results


def normalize(results):
    """Flatten Screenpipe's {type, content} results into a uniform shape.

    Handles both screen and audio results:
      - OCR carries `file_path`/`frame_id` (the screenshot); UI/accessibility does not.
      - Audio carries `transcription` and `speaker` instead of a screenshot.
    Returns events oldest-first. Consecutive screen frames with the same app +
    screenshot are collapsed (Screenpipe records many near-identical frames);
    audio chunks with identical transcription text are likewise collapsed.
    """
    events = []
    for r in results:
        c = r.get("content", {})
        is_audio = r.get("type") == "Audio" or "transcription" in c
        speaker = c.get("speaker") or {}
        events.append(
            {
                "type": r.get("type"),
                "is_audio": is_audio,
                "timestamp": c.get("timestamp"),
                "app_name": c.get("app_name") or (c.get("device_name") if is_audio else None),
                "window_name": c.get("window_name"),
                "text": (c.get("transcription") or c.get("text") or "").strip(),
                "speaker": speaker.get("name") if isinstance(speaker, dict) else c.get("speaker_label"),
                "screenshot": c.get("file_path") if not is_audio else None,
                "frame_id": c.get("frame_id"),
            }
        )

    events.sort(key=lambda e: e["timestamp"] or "")

    deduped = []
    for e in events:
        prev = deduped[-1] if deduped else None
        if prev:
            if e["is_audio"] and prev["is_audio"] and prev["text"] == e["text"]:
                continue
            if not e["is_audio"] and prev["app_name"] == e["app_name"] \
                    and prev["screenshot"] == e["screenshot"]:
                continue
        deduped.append(e)
    return deduped


def _iso_window(timestamp, minutes):
    """Return (start_iso, end_iso) for a ±`minutes` window around an ISO timestamp."""
    ts = datetime.fromisoformat(timestamp)
    return (
        (ts - timedelta(minutes=minutes)).isoformat(),
        (ts + timedelta(minutes=minutes)).isoformat(),
    )


def find_anchors(query, api_key, days=7, per_day=8, gap_minutes=15):
    """Find the distinct moments in time when `query` was relevant.

    Uses the per-day term search to locate matching frames, then clusters their
    timestamps so hits within `gap_minutes` of each other count as one moment
    (avoids pulling overlapping context windows twice). Returns anchor ISO
    timestamps, oldest first.
    """
    frames = normalize(search_window(query, api_key, days, per_day))
    stamps = [f["timestamp"] for f in frames if f["timestamp"]]
    anchors = []
    for ts in stamps:
        if anchors and (datetime.fromisoformat(ts)
                        - datetime.fromisoformat(anchors[-1])) <= timedelta(minutes=gap_minutes):
            continue
        anchors.append(ts)
    return anchors


def context_at(timestamp, api_key, window_minutes=5, per_source=30):
    """Fetch ALL context (OCR + audio) around `timestamp`, unfiltered by term.

    This is the key move: the term anchors *when*, but the substance of a moment
    (e.g. who was in a meeting, what was said) lives in the surrounding audio
    transcript and screen activity — not in frames containing the term. Returns
    a normalized, time-sorted event list mixing screen and audio.
    """
    start, end = _iso_window(timestamp, window_minutes)
    results = []
    for ctype in ("ocr", "audio"):
        # q is required by the API; "" matches everything in the window.
        results.extend(search("", api_key, ctype, per_source, start, end))
    return normalize(results)


def snippet(text, width=80):
    one_line = " ".join(text.split())
    return one_line[:width] + ("…" if len(one_line) > width else "")


def print_human(events):
    if not events:
        print("No history found for that text.")
        return
    for e in events:
        ts = (e["timestamp"] or "").replace("T", " ")[:19]
        print(f"{ts}  {e['app_name'] or '?'} — {e['window_name'] or ''}".rstrip())
        if e["text"]:
            print(f"    {snippet(e['text'])}")
        if e["screenshot"]:
            print(f"    📷 {e['screenshot']}")
        print()


def main():
    p = argparse.ArgumentParser(description="Query Screenpipe for text lineage.")
    p.add_argument("query", nargs="?", help="The highlighted text to trace.")
    p.add_argument(
        "--content-type",
        default="ocr",
        choices=["ocr", "accessibility", "all"],
        help="ocr (has screenshots, default), accessibility (precise text, no image), or all.",
    )
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true", help="Emit normalized JSON.")
    p.add_argument(
        "--check",
        action="store_true",
        help="Run an environment sanity check (python3, API key, Screenpipe reachability) and exit.",
    )
    args = p.parse_args()

    if args.check:
        sys.exit(0 if doctor() else 1)

    if not args.query:
        p.error("the 'query' argument is required (or use --check)")

    api_key = resolve_api_key()
    if not api_key:
        sys.exit(
            "Error: no Screenpipe API key found. Set $SCREENPIPE_API_KEY, add it "
            "to ~/.zshrc, or write it to .screenpipe_key next to this script."
        )

    try:
        results = search(args.query, api_key, args.content_type, args.limit)
    except urllib.error.HTTPError as e:
        sys.exit(f"Screenpipe API error {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach Screenpipe at {API_BASE} — is it running? ({e.reason})")

    events = normalize(results)

    if args.json:
        print(json.dumps(events, indent=2, ensure_ascii=False))
    else:
        print_human(events)


if __name__ == "__main__":
    main()
