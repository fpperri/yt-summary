---
name: yt-summary:ingest-slides
description: >-
  Capture a talk's slides beside its ingested transcript: pull every slide out of the
  video, keep the content ones as images in <output-dir>/<slug>/slides/, transcribe them
  into slides.<slug>.md, and cite them in the analysis and study guide. Use when a video
  already ingested with yt-summary:ingest has slides worth keeping, when the user pastes
  screenshots from a talk and wants them kept with the ingest, or asks to "grab the
  slides", "capture the diagrams", or "add the images to the ingest".
---

# Ingest slides → slides/ · slides.<slug>.md

A transcript loses whatever was only on screen: diagrams, taxonomies, schemas, the exact
wording of a definition. This skill keeps it, as the visual half of the raw capture:
images in `slides/` and their text in `slides.<slug>.md`, beside `transcript.<slug>.md`.

The script (`scripts/slides.py`) does the mechanical part — downloading, scene detection,
sampling, extraction. You do the judging: which frames are content, what each slide says,
how it changes the analysis.

## Before you start

- The video must already be ingested (`transcript.<slug>.md` exists) — run
  `yt-summary:ingest` first. Pass that source folder as `--dir` everywhere.
- `ffmpeg`, `ffprobe` and `yt-dlp` must be on PATH. The script installs nothing; if one is
  missing, run `yt-summary:setup` or install it yourself, then retry.

## Workflow

### 1. Fetch the video (work files stay out of the repo)
```bash
python {plugin_dir}/scripts/slides.py fetch --dir <source folder>
```
`{plugin_dir}` is the plugin's installation directory (the one containing
`scripts/slides.py`). Video stream only, ≤1080p, into
`<OS temp>/ingest-slides/<video_id>/`. Never commit it. `fetch` shells out to
`yt-dlp` directly, so it relies on the same certifi-based trust as the other
`yt-dlp` calls this plugin makes — see `yt-summary:ingest`'s TLS interception
notes if it fails with a certificate error behind a proxy.

### 2. Detect slide changes and build contact sheets
```bash
python {plugin_dir}/scripts/slides.py detect --dir <source folder> --crop <full|aie|overlay|W,H,X,Y>
```
- `--crop` limits detection to the slide area, so the speaker moving doesn't count as a
  change: `full` for full-screen slides; `aie` for a layout with the slide top-right and a
  speaker inset bottom-left (with a sponsor bar below); `overlay` for cards top-left over a
  talking head (proven on one video so far); or `W,H,X,Y` in ffmpeg crop order, expressions
  allowed, e.g. `iw*0.7,ih*0.62,iw*0.29,0`. Run `layout` first (below) if you don't know the
  layout.
- `--threshold` (default `0.04`) sets scene-change sensitivity. Go lower (toward `0.02`) if
  `detect` is missing light or text-only changes; go higher (toward `0.12`) for a busy
  overlay layout that's throwing off false positives.
- It prints every sample as `index first=MM:SS t=SECONDS` and the contact-sheet paths
  (`SHEET=<path>`). Each cell is labelled with its index and time, so read the label, don't
  count cells. Changes closer than `--merge` seconds (animation steps) collapse into one
  sample taken `--settle` seconds after the last. The opening frame is sampled too.
- **`DUP <i> of=<j>`** lines mark a sample whose perceptual hash matched an earlier kept
  one closely enough to call it a duplicate (an animation step, a dimmed repeat) — it's left
  out of the contact sheets by default. Pass `--keep-dups` to include duplicates in the
  sheets anyway, or `--dup-threshold` to loosen (higher) or tighten (lower) the match. It
  compares each sample only against the earliest kept one in its run, so it can also mark a
  later, more-complete step of a bullet-by-bullet build (or a new slide that happens to share
  another's template) as a duplicate of the earlier, less-complete one — if a `SHEET` looks
  like it is missing a slide you know is there, re-run with `--keep-dups` and check the frame
  yourself rather than trusting the DUP line.
- **`BURST <i> ... n=<count> changes`** flags a group of two or more raw scene changes that
  collapsed into one sample — an animation building up over several steps, or a brief flash
  (a one-off slide on screen just a second or two) whose own content the settled sample never
  lands on. Re-sample the burst's span at a higher `--fps` to see the steps individually, as
  the printed line's own suggested command does.
- **`SPARSE <from>..<to>: one sample in <N>s`** flags a stretch longer than `--sparse`
  seconds (default 180) with no detected change — it may be hiding a slide the threshold
  missed. Either lower `--threshold` and re-run `detect`, or re-sample just that span with
  `sample` (below).

### 3. Review the sheets
Read each `SHEET=` image (4×4 labelled cells, in time order). Sort samples into:
- **keep** — content slides: definitions, diagrams, tables, code, data screens.
- **skip** — title cards, speaker-only frames, sponsor cards, dimmed animation steps of a
  slide you keep, and **photographs of people** (don't keep them; name the slide in the
  "Not kept" line instead).

Watch for gaps the sheets don't show clearly, beyond what `SPARSE`/`BURST` already flagged
— a stretch where the transcript names several things quickly but the sheets show only one
frame often hides slides on screen for a second. Re-sample it:
```bash
python {plugin_dir}/scripts/slides.py sample --dir <source folder> --from 12:44 --to 12:53 --crop aie
```
Its 6×6 sheets label each cell with its time in seconds.

### 4. Extract the keepers
```bash
python {plugin_dir}/scripts/slides.py extract --dir <source folder> --pick 209.5=agent-architecture --pick 515.5=memory-system ...
```
Full resolution, WebP (≈60 KB a slide), named `<mmss>-<name>.webp`. Pass the sample's
printed `t=` seconds (or a time from a sample sheet's label) — never a time read off a cell
by position. Existing files are skipped, never replaced; a pick with no frame fails.

`--crop` on `extract` defaults to `full`; passing the same crop used for `detect`/`sample`
can cut off slide content that sat outside the detection region (a footer, a caption), so
prefer `full` unless you have a reason not to.

**Read every extracted slide back at full resolution** before transcribing — a pick can
land on a camera cutaway instead of the slide. `tile` renders reading-sized crops (whole
slide, top half, bottom half, and a footer band) for exactly this:
```bash
python {plugin_dir}/scripts/slides.py tile --dir <source folder>
```
Defaults to every image in `<source folder>/slides/`; pass `--slide <path>` (repeatable) to
tile specific ones instead.

**Screenshots the user supplied** are raw capture too — keep them byte for byte:
```bash
python {plugin_dir}/scripts/slides.py add-shot --dir <source folder> --file <image> --at 04:56 --name human-memory
```
Pasted images only exist on disk if the harness saved them (check its images folder);
extract the same slide from the video for any that didn't land.

### 5. Transcribe → `slides.<slug>.md`
Read each kept slide at full resolution and write `slides.<slug>.md` following
**`references/slides-format.md`**. If the file already exists, add entries for the new
slides only — it is raw capture too. Transcribe what is on the slide, verbatim where it is
text; `[?]` for what you can't read; `[sic]` for the slide's own typos; note slides that
contradict each other or repeat another's text. No interpretation here — that goes in the
analysis.

**Frame chrome is not slide content.** Conference recordings wrap the slide in sponsor
bands, event logos and the speaker's camera inset. A sponsor logo under a slide is not an
attribution or a source — transcribe only what is inside the slide itself.

### 6. Re-run the analysis and study guide
Run `yt-summary:digest` again on the transcript. It reads `slides.<slug>.md` alongside the
transcript and cites slide content as `(slide mm:ss)` beside transcript timestamps, fixes
captioned names the slides spell correctly, and adds what only the slides carried.

## When `detect` finds nothing useful (demo mode)

A live coding demo or a screen recording has no clean slide cuts for `detect` to catch —
the screen is in constant, gradual motion. Skip `detect` entirely and instead sample the
whole video at a low, fixed rate:
```bash
python {plugin_dir}/scripts/slides.py sample --dir <source folder> --from 0:00 --to <end> --fps 0.25 --crop full
```
Read the resulting sheets for views worth keeping (a diagram, a config file, a result
screen), then zoom in around each one at `--fps 2.5` (0.4-second frames) to find the exact
moment it settles before extracting:
```bash
python {plugin_dir}/scripts/slides.py sample --dir <source folder> --from 14:10 --to 14:20 --fps 2.5 --crop full
```

## Layout (unknown crop region)
```bash
python {plugin_dir}/scripts/slides.py layout --dir <source folder> [--at T1,T2,T3,...]
```
Grabs frames at four points in the video (default 10%/35%/60%/85% of its duration; pass
`--at` for specific times) with a 10% grid overlaid (a heavier line at 50%). Read the grid
against the slide region and express it as `W,H,X,Y` fractions for `--crop`, e.g.
`iw*0.7,ih*0.62,iw*0.29,0`.

## Conventions
- **Raw capture is never overwritten.** Slides are added, not replaced; a re-run only fills
  in what's new.
- **Photographs of people are not kept.** Note the slide's time and subject in the "Not
  kept" line of `slides.<slug>.md` instead of extracting the frame.
- **In a vacuum**, as for the other artifacts: the slides of this one talk, no cross-links.
- Report what you kept, what you skipped and why, and any stretch you re-sampled.

## Bundled resources
- `scripts/slides.py` — `fetch`, `detect`, `sample`, `layout`, `extract`, `tile`, `add-shot`
  (stdlib + ffmpeg, ffprobe and yt-dlp)
- `references/slides-format.md` — the `slides.<slug>.md` spec
