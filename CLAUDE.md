# CLAUDE.md — Context Trace

macOS data-lineage tool. Highlight text anywhere → **Show History** → see a
reconstructed story of where that text came from and what was happening around
it, as a chronological timeline with screenshots. Backed by a locally-running
**Screenpipe** instance and a **Gemini** LLM layer that turns raw captures into a
provenance narrative.

**This is a demo**, not a product. Two goals in tension: (1) fast/easy to build,
(2) looks reasonably nice (no terminal UI). Bias toward quick-and-dirty; avoid
heavy tooling (no Xcode, no app bundles, no signing). Stdlib-only Python — no
`pip install` (we call Gemini over plain `urllib`, not the SDK).

> [lineage-spec.md](lineage-spec.md) is the **archived original brief** —
> superseded by this file. The spec was wrong about auth, the response schema,
> and the stack, and it predates the biggest design change (see below). This
> file wins where they disagree.

## The key insight (why this isn't a string search)

The spec imagined "find every frame containing this text, list them." Building it
proved that's weak: a query like a **person's name** barely appears in screen OCR
(you mostly see yourself *looking them up*), and the actual substance — e.g. a
meeting — lives in **audio transcripts**, not screen text.

So the model is **anchor-in-time, then pull context**:

1. **Anchor** — use the term to find *when* it was relevant (the few frames that
   match → their timestamps, clustered into distinct moments).
2. **Expand** — for each moment, pull **all** context in a ±N-minute window
   (OCR *and* audio), unfiltered by the term.
3. **Synthesize** — hand that mixed context to Gemini, which writes a provenance
   narrative + a deduplicated list of distinct events.

The term is a *time anchor*, not a filter on content. This is what lets the tool
say "you had a meeting with X about Y" when the name itself was barely on screen.

## Pipeline & files

`python3 render.py "term"` runs the whole thing:

```
query.py        find_anchors(term) → context_at(ts) per anchor   (raw OCR + audio)
   ↓
analyze.py      Gemini → { narrative, moments[{anchor_index, label}] }
   ↓
render.py       open browser on "working…" page → run pipeline in background →
                swap to overview (narrative + clickable moments) via local server
```

- **`config.py`** — shared key resolver: `get_key(name)` checks env var, then a
  gitignored `config.json` next to the scripts. Used for both `SCREENPIPE_API_KEY`
  and `GEMINI_API_KEY`. The config file is what makes keys work in the macOS
  Quick Action (which has neither shell config nor exported env).
- **`query.py`** — Screenpipe access. `search()`, `search_window()` (per-day
  sampling), `find_anchors()` (term → distinct moments), `context_at()` (±window
  OCR+audio pull), `normalize()` (unifies screen + audio events), and
  `--check` (env/auth/reachability doctor).
- **`analyze.py`** — two tiers. `analyze_overview(term, frames, anchors, key)`
  returns `{narrative, moments[{anchor_index, label}]}` (fast — narrative + an
  ≤8-word label per moment; the structured per-event version doubled latency).
  `analyze_moment(term, frames, key)` returns a focused `{detail}` paragraph for
  one zoomed-in moment (Stage 2). Gemini `gemini-3.5-flash`, REST, structured JSON.
- **`render.py`** — runs the pipeline and serves the overview. Flags: `--days`
  (lookback, default 7), `--window` (context minutes, default 5).
- **`show-history.sh`** + **`build/ContextTrace.workflow/`** — the macOS Quick
  Action wrapper and bundle (Phase 3). The wrapper uses absolute paths (Service
  PATH is minimal) and runs `render.py`; keys come from `config.json`.

## Screenpipe API — verified facts

- **Base URL:** `http://localhost:3030`. **Endpoint:** `GET /search`.
- **Auth REQUIRED:** `Authorization: Bearer <key>` or **403**. Resolved via
  `config.py` (env → `config.json`).
- **Params:** `q` (required; `""` matches everything), `content_type`
  (`ocr` | `accessibility` | `audio` | `all`), `limit`, `start_time`/`end_time`
  (ISO-8601 window). Results are newest-first — hence per-day sampling, so a busy
  recent day doesn't bury older moments.
- **Response:** `{ "data": [ { "type", "content": {...} } ], "pagination": {...} }`.
  - **OCR/screen** `content`: `app_name`, `window_name`, `text`, `timestamp`,
    `file_path`, `frame_id`.
  - **Audio** `content`: `transcription`, `text`, `timestamp`, `start_time`/
    `end_time`, `speaker` (+ `device_name`, `file_path` → an `.mp4`).
  - **UI/accessibility** `content`: `text`/`timestamp` but **no screenshot**.

### Gotchas (all learned from real data, all handled in code)
- **Names are weak keys.** A person's name returns few/no matches in OCR and often
  0 in audio. Don't search content by name — anchor in time and read the context.
- **Audio is the meeting.** Meetings are spoken; their signal is in `audio`
  transcripts, not screen OCR. Whisper-Tiny output is rough — infer, don't quote.
- **Speaker diarization is unreliable** here (`speaker.name` often empty) — expect
  *what* was said, not always *who*.
- **Not every screen `file_path` is an image.** Some are compacted `.mp4` video
  segments — `render.py` only copies files with image extensions (`IMAGE_EXTS`);
  others fall back to the "no screenshot" placeholder.
- **OCR `text` is the whole screen**, and app icon fonts OCR into Unicode
  private-use glyphs (`analyze.py._strip_pua` removes them).

## UI: generated HTML in the browser (no native window)

Full CSS control, stdlib-only. Chrome blocks a `file://` page from loading
`file://` subresources, so `render.py` serves over a **local stdlib
`http.server`** (an `http://localhost:PORT` origin → relative-path images load in
Chrome), never opening the file directly.

Because the Gemini call is ~15s (and that latency is intrinsic — input size
barely affects it, output volume does), the server comes up **immediately** on a
"working…" spinner page, runs the pipeline in a background thread, and the page
polls `status.json` and swaps to the result when ready (`serve_with_progress`).
The server shuts down shortly after the result loads (hard cap if compute hangs).

The result page is the **overview**: a narrative paragraph headline, then a tight
list of clickable **moments** (each = an anchor: ≤8-word label + app/time + a
screenshot thumbnail, or 🎙 for audio-only moments).

Rejected: base64-embedded images (bloats HTML, files not inspectable); native
toolkits (Tkinter dated, PyQt heavy, SwiftUI needs Xcode).

## Status

- ✅ **Phase 1 — `query.py`**: term/time/window/audio fetch + `--check`.
- ✅ **LLM layer — `analyze.py`** + **`config.py`**: verified against live Gemini.
- ✅ **Phase 2 — `render.py`**: two-tier overview (narrative + labeled moments),
  audio-aware, with the instant "working…" page.
- ✅ **Phase 3 — macOS Quick Action**: `show-history.sh` + `ContextTrace.workflow`,
  installed to `~/Library/Services/`. Highlight text → Services → "Show History" →
  spinner → overview. Verified working in a stripped Service environment.
- ⏳ **Stage 2 — click-to-zoom**: deferred. Clicking a moment should re-run
  `analyze_moment` on just that anchor (tighter window/frames → richer detail)
  and render it inline. The long-lived server in `serve_with_progress` is the
  foundation; needs a `/zoom?anchor=N` route + idle-timeout lifecycle.

### Open / tunable
- **Gemini latency (~15s) is intrinsic** — output volume drives it, not input.
  Don't try to fix it by trimming frames (measured: no effect, and hurts the
  narrative). The "working…" page addresses *perceived* latency instead.
- **Window breadth tradeoff:** ±5 min pulls real but incidental activity into the
  story. Narrower = tighter but risks clipping a meeting. Tune `--window`.
- **macOS notifications** from the Service land silently in Notification Center
  (low priority) — not worth fixing; the browser working-page is the feedback.
- Deep-linking back into the source app is **out of scope** (unstable); the
  screenshot/transcript is the context.

## Conventions

- **Stdlib-only.** Justify any new dependency before adding it.
- **Verify against the running API, never assume the schema.** The spec was wrong
  about auth, the response shape, and which content type carries the signal.
- **Never commit keys.** `config.json` is gitignored.
