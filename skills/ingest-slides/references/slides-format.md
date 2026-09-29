# slides.&lt;slug&gt;.md — the visual capture

The text of every kept slide, in time order: the visual counterpart of
`transcript.<slug>.md`. Faithful to the screen, in a vacuum, no interpretation.

Write to `<output-dir>/<slug>/slides.<slug>.md`, images in `<output-dir>/<slug>/slides/`:

```markdown
---
source: <url>
type: slides
video_id: <id>
title: <title>
channel: <channel>
published: <date>
retrieved: <today YYYY-MM-DD>
captured_via: <N screenshots supplied by <person> (byte-identical copies) + M frames extracted with ffmpeg from the <height>p stream; K slides in slides/>
---

# <Short title> — slides (visual capture)

One paragraph: what this file is, the marks used — timestamps are when the slide first
appears (mm:ss); **(s)** = screenshot supplied; **(x)** = frame extracted; `[?]` unreadable;
`[sic]` the slide's own error — and what kinds of frames were not kept.

## <Section named by what the talk does here>

**mm:ss · <Slide title as shown>** (x) — `slides/<mmss>-<name>.webp`
<The slide's text, verbatim where it is text; structure (boxes, arrows, tables) described
plainly. Bold what the slide bolds. Note animation build-ups and how long a slide is on
screen when it is under a few seconds.>

...

Not kept: <each skipped content-bearing slide, one clause each, with its time — title
cards, sponsor slides, photographs of people>.
```

## Rules
- Every image in `slides/` has an entry; every entry names its image.
- Verbatim text in quotes only when copied exactly; otherwise describe.
- Flag contradictions between slides, and slides that repeat another's text, where they
  occur — the analysis will want them.
- Never name or describe people in photographs beyond what a caption on the slide says.
