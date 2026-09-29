---
name: yt-summary:digest
description: Turn a saved transcript or any text file into an analysis and a study guide written beside it. Use when the user wants a source broken down, analyzed, studied, or turned into study material, or points at a transcript, article, notes, or pasted text and wants more than a summary. Works on any text — it never fetches a URL.
---

# Digest a source → analysis · study guide

Read one text source and write two artifacts next to it: an **analysis** (what the
source says, and how much of it is load-bearing) and a **study guide** (how to
actually learn it). Both are written to be read on their own, without the reader
going back to the source.

This skill does not fetch anything. It works on a file that already exists, or on
text the user pastes. To pull a video down first, use `yt-summary:ingest`.

## Input

Accept either:

- **A file path** — a transcript from `yt-summary:ingest`, or any `.md`/`.txt`.
- **Pasted text** — save it first, so the artifacts have something to sit beside.
  Write it to `<output-dir>/<name>/source.<name>.md` where `<name>` is a kebab-case
  slug you derive from the content's subject, then treat that as the file path.
  Ask the user where to put it if the destination isn't obvious.

Read the whole source before writing anything. If it is long, read it in chunks —
do not skim, and do not write the analysis from the first section alone.

## Output

Both artifacts go in the **same directory as the source**, named from the source's
stem with its kind prefix replaced:

| Source | Analysis | Study guide |
|---|---|---|
| `transcript.some-channel-a-talk.md` | `analysis.some-channel-a-talk.md` | `study-guide.some-channel-a-talk.md` |
| `notes.md` | `analysis.notes.md` | `study-guide.notes.md` |

Follow the two format specs exactly:

- **`references/analysis-format.md`**
- **`references/study-guide-format.md`**

## Workflow

1. **Resolve the source** — file path, or save pasted text as above. Confirm the
   resolved path with the user if you had to guess it.
2. **Read it fully.**
3. **Write the analysis** following `references/analysis-format.md`.
4. **Write the study guide** following `references/study-guide-format.md`, reading
   the analysis you just wrote as well as the source — the guide builds on it.
5. **Self-check** — re-read every quote, timestamp, and `*claim*`/`*inference*`
   mark in both artifacts against the source. Fix any mismatch before reporting;
   a wrong timestamp or a spliced quote is a defect, not a rounding error.
6. **Report** the two paths you wrote, and anything about the source that limits
   the result (truncated transcript, missing audio, heavy cross-talk).

## Conventions

- **Locators, always.** Every specific claim cites where it came from: `(12:34)`
  for a timestamped transcript, `(§ Heading)` or a short quoted phrase for prose.
  A reader must be able to get back to the passage.
- **Separate what is shown from what is asserted.** A source demonstrating a
  result and a source stating one are different things; the analysis format has a
  section for exactly this. Mark unbacked assertions `*claim*`.
- **Never present your own inference as the source's content.** A structure,
  grouping, count, principle list, or claim you assembled from the source is your
  synthesis, not something the source states outright — mark it `*inference*` and
  locate the nearest source passage it's drawn from. ("Four principles" is only
  the source's if the source said four; otherwise it's yours to label.)
- **Quotes are verbatim and contiguous.** Never splice separate passages into one
  quote. An ellipsis may only trim words out of the middle of one contiguous
  passage — it never stands in for joining two separate ones. A quote's
  timestamp is the line it actually comes from, not the surrounding passage's.
- **Read slides alongside the transcript.** If `slides.<slug>.md` exists beside
  the source (written by `yt-summary:ingest-slides`), read it too. Cite slide
  content as `(slide mm:ss)` next to the transcript locator, and note where
  narration and slides disagree. Sponsor bands, event logos, and other frame
  chrome are not slide content or attribution.
- **A slide-corrected name is a note, not a silent edit.** When the slide spells
  a name differently from the captions, use the slide's spelling but mark the
  correction inline the first time it appears — `Alyx (slide 04:12; captions:
  Alex)` — rather than picking one spelling and moving on.
- **Faithful to this one source.** No cross-references to other material, no
  outside facts smuggled in. If the source is wrong, note that it is wrong — don't
  quietly correct it.
- **The source stays the source of truth.** Both artifacts are derived and can be
  regenerated from it; never edit the source to fit the digest.

## Bundled resources

- `references/analysis-format.md` — the analysis spec
- `references/study-guide-format.md` — the study-guide spec
