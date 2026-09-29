# Analysis format

The analysis answers **"what does this source actually say, and how much of it is
load-bearing?"** for a reader who will not open the source.

It is not a summary. A summary shortens; an analysis takes a position on what
matters, what is supported, and what is merely asserted.

## File

`analysis.<stem>.md`, in the same directory as the source.

## Structure

````markdown
---
source: <path or URL the analysis is based on>
type: analysis
tags: [tag-one, tag-two]
generated: YYYY-MM-DD
---

# Analysis — <source title>

## In one paragraph
What this source is, who it is for, and what it is trying to establish. Three to
five sentences. A reader who stops here should still know whether the source is
worth their time.

## Key points
The substantive content, most important first. One bullet per idea, each with a
locator. Six to twelve bullets — if you have more, you are transcribing rather
than analyzing.

- **Short claim in bold.** The supporting detail in a sentence or two. (12:34)

## Evidence vs assertion
The section that earns the file. Split what the source *shows* from what it
*states*, and say which is which.

| Statement | Backed by | Read as |
|---|---|---|
| "X doubles throughput" | a benchmark shown on screen (14:02) | evidence |
| "most teams do Y" | nothing — stated in passing (21:40) | *claim* |

If everything in the source is assertion, say so plainly. That is a finding.

If slides accompany the source, this is where slide-backed claims and
speech-only claims are told apart — a number or attribution shown on a slide
reads differently than the same one only spoken.

## Terms
Vocabulary the source introduces or uses in a specific way. Only terms a reader
would otherwise have to look up.

- **Term** — what it means *as this source uses it*. (04:10)

## Open questions
What the source raises and does not settle: unanswered objections, missing
conditions, things asserted without a mechanism. Three to six bullets. This is
where a critical reader's doubts get recorded rather than smoothed over.

## Tags
Five to ten lowercase keywords, comma-separated. Subject matter, not format.
````

## Rules

- **Locate every specific claim.** `(mm:ss)` for timestamped transcripts,
  `(§ Heading)` or a distinctive quoted phrase for prose.
- **Mark unbacked assertions `*claim*`.** Applies in Key points too, not only in
  the table.
- **Mark your own synthesis `*inference*`.** A grouping, count, or principle
  list you assembled is not the source's content until the source states it —
  say so and locate the passage it's drawn from, in Key points as much as
  anywhere.
- **Quotes are verbatim and contiguous.** Never splice separate passages into
  one quote; trim only with an ellipsis, and give the timestamp of the line the
  quote actually comes from.
- **If `slides.<slug>.md` exists beside the source, read it.** Cite slide
  content as `(slide mm:ss)` beside the transcript locator, prefer its spelling
  of names over garbled captions, and note narration/slide disagreements in
  Evidence vs assertion. Frame chrome (sponsor bands, event logos) isn't slide
  content.
- **In a vacuum.** No references to other sources, no outside knowledge. Tags are
  the only mechanism connecting this to anything else.
- **Don't pad.** A thin source gets a short analysis. Empty sections are removed,
  not filled — except *Evidence vs assertion*, which is always present, because
  "none of this is demonstrated" is precisely the thing worth reporting.
