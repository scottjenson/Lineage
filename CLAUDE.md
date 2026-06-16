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
analyze.py      Gemini → { narrative, events[{summary, frame_index, ...}] }
   ↓
render.py       HTML vertical timeline → temp dir → local http.server → browser
```

- **`config.py`** — shared key resolver: `get_key(name)` checks env var, then a
  gitignored `config.json` next to the scripts. Used for both `SCREENPIPE_API_KEY`
  and `GEMINI_API_KEY`. The config file is what makes keys work in the eventual
  Quick Action (which has neither shell config nor exported env).
- **`query.py`** — Screenpipe access. `search()`, `search_window()` (per-day
  sampling), `find_anchors()` (term → distinct moments), `context_at()` (±window
  OCR+audio pull), `normalize()` (unifies screen + audio events), and
  `--check` (env/auth/reachability doctor).
- **`analyze.py`** — `analyze(term, frames, key)` calls Gemini (`gemini-3.5-flash`,
  REST, structured-JSON output) and returns `{narrative, events}`. Each event has
  `frame_index` linking back to the source frame for evidence.
- **`render.py`** — builds the timeline HTML and shows it. Flags: `--days`
  (lookback, default 7), `--window` (context minutes, default 5).

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
`file://` subresources, so `render.py` does NOT open the file directly:
1. writes `index.html` + copies referenced screenshots into a temp dir,
2. references them with **relative paths** (`./frame_NN.jpg`),
3. starts a **short-lived stdlib `http.server`** on a random port,
4. opens `http://localhost:PORT/` (an `http://` origin → images load in Chrome),
5. the server auto-stops a few seconds after the page is served.

Audio events render as a 🎙 transcript card (no screenshot); screen events show
the screenshot as evidence. The narrative is a headline above the timeline.

Rejected: base64-embedded images (bloats HTML, files not inspectable); native
toolkits (Tkinter dated, PyQt heavy, SwiftUI needs Xcode).

## Status

- ✅ **Phase 1 — `query.py`**: done. Term/time/window/audio fetch + `--check`.
- ✅ **LLM layer — `analyze.py`** + **`config.py`**: done, verified against live
  Gemini + Screenpipe.
- ✅ **Phase 2 — `render.py`**: done. Narrative-led audio-aware timeline in Chrome.
- ⏳ **Phase 3 — macOS Quick Action**: not started. A thin shell script that
  captures the selection and runs `render.py`. Keys come from `config.json` (the
  Service env has no shell exports). Build and verify the pipeline standalone
  first (already done), then wrap it.

### Open / tunable
- **Window breadth tradeoff:** ±5 min pulls real but incidental activity (e.g.
  unrelated browsing) into the story. Narrower = tighter but risks clipping a
  meeting. Tune `--window` per demo.
- Deep-linking back into the source app is **out of scope** (unstable); the
  screenshot/transcript is the context.

## Conventions

- **Stdlib-only.** Justify any new dependency before adding it.
- **Verify against the running API, never assume the schema.** The spec was wrong
  about auth, the response shape, and which content type carries the signal.
- **Never commit keys.** `config.json` is gitignored.
