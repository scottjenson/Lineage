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


# Both pages poll status.json; when state flips from "working" the page reloads
# to pick up the freshly-written result (or error) HTML.
_POLL_SCRIPT = """
<script>
  async function poll() {
    try {
      const r = await fetch('status.json?' + Date.now());
      const s = await r.json();
      if (s.state !== 'working') { location.reload(); return; }
    } catch (e) {}
    setTimeout(poll, 1000);
  }
  poll();
</script>
"""


def build_working_html(query_text):
    """Instant placeholder shown while the pipeline runs in the background."""
    q = html.escape(query_text)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Context Trace — “{q}”</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    margin: 0; min-height: 100vh; display: grid; place-items: center;
    background: #f6f7f9; color: #1c1d21;
  }}
  @media (prefers-color-scheme: dark) {{ body {{ background: #16171b; color: #e6e7ea; }} }}
  .box {{ text-align: center; }}
  .spinner {{
    width: 32px; height: 32px; margin: 0 auto 16px; border-radius: 50%;
    border: 3px solid rgba(47,111,237,.25); border-top-color: #2f6fed;
    animation: spin 0.8s linear infinite;
  }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  .q {{ color: #2f6fed; font-weight: 600; }}
  .sub {{ color: #8a8d96; font-size: 13px; margin-top: 6px; }}
</style></head>
<body>
  <div class="box">
    <div class="spinner"></div>
    <div>Tracing <span class="q">“{q}”</span>…</div>
    <div class="sub">Searching captures and reconstructing the story</div>
  </div>
  {_POLL_SCRIPT}
</body></html>"""


def build_error_html(query_text, message):
    """Shown if the background pipeline fails."""
    q = html.escape(query_text)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Context Trace — error</title>
<style>
  body {{ font: 15px/1.5 -apple-system, sans-serif; margin: 0; min-height: 100vh;
         display: grid; place-items: center; background: #f6f7f9; color: #1c1d21; }}
  @media (prefers-color-scheme: dark) {{ body {{ background: #16171b; color: #e6e7ea; }} }}
  .box {{ max-width: 480px; text-align: center; }}
  code {{ display: block; margin-top: 10px; color: #c0392b; font-size: 13px; }}
</style></head>
<body><div class="box">
  <div>Couldn’t trace <strong>“{q}”</strong>.</div>
  <code>{html.escape(message)}</code>
</div></body></html>"""


def _bundle_app(bundle):
    """Most representative app name for a moment (first non-audio screen frame)."""
    for f in bundle:
        if not f["is_audio"] and f.get("app_name"):
            return f["app_name"]
    return bundle[0]["app_name"] if bundle else ""


def _bundle_thumb(bundle, workdir, i):
    """Copy one displayable screenshot from a moment's bundle; return rel name or None.

    Some screen frames point at compacted .mp4 video, not images — skip those.
    """
    for f in bundle:
        src = f.get("screenshot")
        if src and Path(src).suffix.lower() in IMAGE_EXTS and Path(src).exists():
            name = f"moment_{i:02d}{Path(src).suffix}"
            shutil.copyfile(src, workdir / name)
            return name
    return None


def build_html(narrative, moments, query_text):
    """Render the overview: narrative paragraph + a tight list of moments.

    `moments` is a list of dicts: {index, label, app, timestamp, image (rel
    filename or None)}. Each row is clickable (Stage 2 will wire the zoom).
    """
    rows = []
    for m in moments:
        label = html.escape(m.get("label") or "(moment)")
        app = html.escape(m.get("app") or "")
        ts = (m.get("timestamp") or "").replace("T", " ")[11:19]  # HH:MM:SS
        day = (m.get("timestamp") or "")[:10]
        meta = " · ".join(p for p in (app, f"{day} {ts}".strip()) if p)
        img = m.get("image")
        thumb = (
            f'<img class="thumb" src="./{html.escape(img)}" loading="lazy" alt="">'
            if img
            else '<div class="thumb noshot">🎙</div>'
        )
        rows.append(f"""
        <li class="moment" data-anchor="{m['index']}">
          {thumb}
          <div class="mtext">
            <p class="label">{label}</p>
            <div class="meta">{html.escape(meta)}</div>
          </div>
        </li>""")

    body = "\n".join(rows) if rows else '<p class="empty">No moments found.</p>'
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
  .sub {{ color: #8a8d96; font-size: 13px; margin: 18px 0 8px; font-weight: 600;
          text-transform: uppercase; letter-spacing: .04em; }}
  ul.moments {{ list-style: none; max-width: 720px; margin: 0 auto; padding: 0; }}
  .moment {{
    display: flex; gap: 12px; align-items: center; background: #fff;
    border-radius: 10px; padding: 10px 12px; margin-bottom: 8px; cursor: pointer;
    box-shadow: 0 1px 2px rgba(0,0,0,.06); transition: box-shadow .12s, transform .12s;
  }}
  .moment:hover {{ box-shadow: 0 2px 10px rgba(47,111,237,.18); transform: translateY(-1px); }}
  @media (prefers-color-scheme: dark) {{ .moment {{ background: #22242a; box-shadow: none; }} }}
  .thumb {{
    width: 96px; height: 60px; object-fit: cover; border-radius: 6px; flex: none;
    border: 1px solid rgba(0,0,0,.08); background: rgba(0,0,0,.03);
  }}
  .thumb.noshot {{ display: grid; place-items: center; font-size: 22px; }}
  .mtext {{ min-width: 0; }}
  .label {{ margin: 0 0 3px; font-weight: 500; }}
  .meta {{ color: #8a8d96; font-size: 12px; font-variant-numeric: tabular-nums; }}
  .empty {{ text-align: center; color: #8a8d96; max-width: 720px; margin: 40px auto; }}
</style>
</head>
<body>
  <header>
    <h1>Data lineage for <span class="q">“{q}”</span></h1>
    <div class="narrative">{narrative_html}</div>
    <div class="sub">{len(moments)} moment(s)</div>
  </header>
  <ul class="moments">
    {body}
  </ul>
</body>
</html>
"""


def serve_with_progress(workdir, query_text, compute, lifetime=8.0):
    """Serve `workdir` on a random free port, open it in the browser, then stop.

    Opens immediately on a placeholder page, runs `compute` in the background,
    and the page polls status.json to swap to the result when ready. The server
    is bound to localhost only and shuts down `lifetime` seconds after the result
    is ready (or after a generous cap if compute hangs).
    """
    # Initial state: placeholder page + working status, so the browser has
    # something to show the instant it opens.
    (workdir / "status.json").write_text('{"state": "working"}')
    (workdir / "index.html").write_text(build_working_html(query_text), encoding="utf-8")

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(workdir)
    )
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    url = f"http://localhost:{port}/index.html"
    print(f"Serving at {url}")
    webbrowser.open(url)

    def run():
        try:
            html_out = compute(workdir)
        except Exception as e:  # noqa: BLE001 — show the failure on the page, don't crash silently
            html_out = build_error_html(query_text, str(e))
        (workdir / "index.html").write_text(html_out, encoding="utf-8")
        (workdir / "status.json").write_text('{"state": "ready"}')
        # Give the page time to reload + load its images before we stop serving.
        threading.Timer(lifetime, httpd.shutdown).start()

    threading.Thread(target=run, daemon=True).start()
    # Hard cap so a hung compute can't keep the process (and server) alive forever.
    threading.Timer(180, httpd.shutdown).start()
    httpd.serve_forever()


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

    def compute(workdir):
        """Run the full pipeline and return the result HTML. Runs in background
        AFTER the browser is already showing the working page."""
        # The term anchors WHEN it was relevant; the substance of each moment
        # (e.g. a meeting's audio) is pulled from unfiltered context around that
        # time. Keep each anchor's bundle so a moment can supply its screenshot.
        anchors = query.find_anchors(args.query, sp_key, days=args.days)
        bundles = [query.context_at(ts, sp_key, window_minutes=args.window) for ts in anchors]
        frames = [f for b in bundles for f in b]
        if not frames:
            return build_error_html(args.query, "No history found for that text.")

        result = analyze.analyze_overview(args.query, frames, anchors, gemini_key)
        narrative = result.get("narrative", "")
        labels = {m["anchor_index"]: m.get("label", "") for m in result.get("moments", [])}

        moments = []
        for i, (ts, bundle) in enumerate(zip(anchors, bundles)):
            moments.append({
                "index": i,
                "label": labels.get(i, ""),
                "timestamp": ts,
                "app": _bundle_app(bundle),
                "image": _bundle_thumb(bundle, workdir, i),
            })
        return build_html(narrative, moments, args.query)

    workdir = Path(tempfile.mkdtemp(prefix="context-trace-"))
    serve_with_progress(workdir, args.query, compute)


if __name__ == "__main__":
    main()
