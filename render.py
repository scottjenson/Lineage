#!/usr/bin/env python3
"""Context Trace — Phase 2 UI.

Takes a text string, runs the Phase 1 query, and shows the results as a clean
vertical timeline in the browser.

Because Chrome blocks file:// pages from loading file:// images, we don't open
the HTML directly. Instead we copy the relevant screenshots into a temp dir
alongside an index.html that references them with relative paths, then serve that
dir over a short-lived local http.server so Chrome sees an http:// origin. The
server shuts down a few seconds after the page is served.

Stdlib only — no pip install required.

Usage:
    python3 render.py "123 Main St"
    python3 render.py "123 Main St" --content-type all --limit 20
"""

import argparse
import functools
import html
import http.server
import shutil
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

import analyze
import config
import query

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def build_html(narrative, events, query_text, image_for_frame, frame_meta):
    """Render the analyzed provenance as a vertical-timeline HTML document.

    `events` are the LLM-summarized distinct events (each with `frame_index`).
    `image_for_frame[idx]` is the relative screenshot filename for frame `idx`,
    or None. `frame_meta[idx]` carries {is_audio, speaker} for that frame so audio
    events render as a transcript card rather than a missing screenshot. The
    narrative is shown as a headline above the timeline.
    """
    cards = []
    for e in events:
        summary = html.escape(e.get("summary") or "")
        app = html.escape(e.get("app") or "")
        ts = (e.get("timestamp") or "").replace("T", " ")[:19]
        idx = e.get("frame_index")
        meta_info = frame_meta.get(idx, {})
        img = image_for_frame.get(idx)

        if meta_info.get("is_audio"):
            spk = html.escape(meta_info.get("speaker") or "")
            badge = f'🎙 audio{" · " + spk if spk else ""}'
            evidence = f'<div class="badge">{badge}</div>'
        elif img:
            evidence = f'<img class="shot" src="./{html.escape(img)}" loading="lazy" alt="screenshot">'
        else:
            evidence = '<div class="shot noshot">no screenshot</div>'

        meta = " · ".join(p for p in (app, ts) if p)
        cards.append(f"""
        <li class="event">
          <div class="dot"></div>
          <div class="card">
            <p class="summary">{summary}</p>
            <div class="meta">{html.escape(meta)}</div>
            {evidence}
          </div>
        </li>""")

    body = "\n".join(cards) if cards else '<p class="empty">No history found.</p>'
    q = html.escape(query_text)
    narrative_html = html.escape(narrative) if narrative else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Context Trace — “{q}”</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    margin: 0; padding: 32px 24px 64px; background: #f6f7f9; color: #1c1d21;
  }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #16171b; color: #e6e7ea; }}
    .card, .narrative {{ background: #22242a; box-shadow: none; }}
  }}
  header {{ max-width: 720px; margin: 0 auto 24px; }}
  h1 {{ font-size: 20px; margin: 0 0 12px; font-weight: 600; }}
  h1 .q {{ color: #2f6fed; }}
  .narrative {{
    background: #fff; border-left: 3px solid #2f6fed; border-radius: 10px;
    padding: 14px 16px; font-size: 15px; line-height: 1.55;
    box-shadow: 0 1px 3px rgba(0,0,0,.08);
  }}
  .sub {{ color: #8a8d96; font-size: 13px; margin: 14px 0 0; }}
  ul.timeline {{
    list-style: none; max-width: 720px; margin: 0 auto; padding: 0;
    position: relative;
  }}
  ul.timeline::before {{
    content: ""; position: absolute; left: 7px; top: 6px; bottom: 6px;
    width: 2px; background: #d6d9e0;
  }}
  @media (prefers-color-scheme: dark) {{ ul.timeline::before {{ background: #34373f; }} }}
  .event {{ position: relative; padding: 0 0 22px 36px; }}
  .dot {{
    position: absolute; left: 0; top: 6px; width: 16px; height: 16px;
    border-radius: 50%; background: #2f6fed; border: 3px solid #f6f7f9;
  }}
  @media (prefers-color-scheme: dark) {{ .dot {{ border-color: #16171b; }} }}
  .card {{
    background: #fff; border-radius: 12px; padding: 14px 16px;
    box-shadow: 0 1px 3px rgba(0,0,0,.08);
  }}
  .summary {{ margin: 0 0 6px; font-weight: 500; }}
  .meta {{ color: #8a8d96; font-size: 12px; font-variant-numeric: tabular-nums; }}
  .shot {{
    display: block; width: 100%; border-radius: 8px; margin-top: 6px;
    border: 1px solid rgba(0,0,0,.08);
  }}
  .noshot {{
    display: grid; place-items: center; height: 80px; color: #9a9da6;
    font-size: 13px; background: rgba(0,0,0,.03);
  }}
  .badge {{
    display: inline-block; margin-top: 4px; padding: 3px 10px; font-size: 12px;
    border-radius: 999px; background: rgba(47,111,237,.12); color: #2f6fed;
  }}
  .empty {{ text-align: center; color: #8a8d96; max-width: 720px; margin: 40px auto; }}
</style>
</head>
<body>
  <header>
    <h1>Data lineage for <span class="q">“{q}”</span></h1>
    <div class="narrative">{narrative_html}</div>
    <div class="sub">{len(events)} distinct event(s)</div>
  </header>
  <ul class="timeline">
    {body}
  </ul>
</body>
</html>
"""


def serve_and_open(workdir, lifetime=8.0):
    """Serve `workdir` on a random free port, open it in the browser, then stop.

    The server is bound to localhost only and shuts down after `lifetime`
    seconds — long enough for the page (and its images) to load, short enough to
    leave nothing running after the demo.
    """
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(workdir)
    )
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]

    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://localhost:{port}/index.html"
    print(f"Serving timeline at {url} (auto-stops in {lifetime:.0f}s)")
    webbrowser.open(url)

    timer = threading.Timer(lifetime, httpd.shutdown)
    timer.start()
    timer.join()


def main():
    p = argparse.ArgumentParser(description="Show a text's data lineage as a timeline.")
    p.add_argument("query", help="The highlighted text to trace.")
    p.add_argument("--days", type=int, default=7, help="How many days back to look for the term (default 7).")
    p.add_argument("--window", type=int, default=5, help="Context window in minutes around each moment (default 5).")
    args = p.parse_args()

    sp_key = query.resolve_api_key()
    if not sp_key:
        sys.exit("No Screenpipe API key found. Run `python3 query.py --check` for details.")
    gemini_key = config.require_key("GEMINI_API_KEY")

    # The term anchors WHEN it was relevant; the substance of each moment (e.g. a
    # meeting's audio) is then pulled from unfiltered context around that time.
    try:
        anchors = query.find_anchors(args.query, sp_key, days=args.days)
        frames = []
        for ts in anchors:
            frames.extend(query.context_at(ts, sp_key, window_minutes=args.window))
    except Exception as e:  # noqa: BLE001 — surface any fetch failure plainly for the demo
        sys.exit(f"Query failed: {e}. Run `python3 query.py --check`.")

    if not frames:
        sys.exit("No history found for that text.")

    try:
        result = analyze.analyze(args.query, frames, gemini_key)
    except Exception as e:  # noqa: BLE001 — surface analysis failure plainly for the demo
        sys.exit(f"Analysis failed: {e}")
    narrative, events = result.get("narrative", ""), result.get("events", [])

    # Copy only the screenshots the LLM's events actually reference, keyed by
    # frame index so build_html can look each one up.
    workdir = Path(tempfile.mkdtemp(prefix="context-trace-"))
    image_for_frame = {}
    frame_meta = {}
    for e in events:
        idx = e.get("frame_index")
        if idx is None or not (0 <= idx < len(frames)):
            continue
        frame = frames[idx]
        frame_meta[idx] = {"is_audio": frame["is_audio"], "speaker": frame.get("speaker")}
        src = frame["screenshot"]
        # Only image files are displayable; some screen frames point at a
        # compacted .mp4 video segment, which can't render in an <img>.
        if src and Path(src).suffix.lower() in IMAGE_EXTS and Path(src).exists():
            name = f"frame_{idx:02d}{Path(src).suffix}"
            shutil.copyfile(src, workdir / name)
            image_for_frame[idx] = name

    (workdir / "index.html").write_text(
        build_html(narrative, events, args.query, image_for_frame, frame_meta),
        encoding="utf-8",
    )
    serve_and_open(workdir)


if __name__ == "__main__":
    main()
