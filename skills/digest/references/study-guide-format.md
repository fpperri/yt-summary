# Study guide format

The study guide answers **"how do I actually learn this?"** It is built for
someone who intends to retain the material, not skim it.

Where the analysis takes a position on the content, the guide is instructional:
objectives, a walkable outline, plain-language explanations, and questions the
reader can test themselves against days later.

## File

`study-guide.<stem>.md`, in the same directory as the source.

## Structure

````markdown
---
source: <path or URL the guide is based on>
type: study-guide
tags: [tag-one, tag-two]
generated: YYYY-MM-DD
---

# Study guide — <source title>

## What you'll be able to do
Three to six concrete capabilities, each starting with a verb. Not "understand
X" — something you could check.

- Explain why X fails when the input is unsorted.
- Choose between X and Y for a given workload, and justify it.

## Outline
The source's actual structure, with locators, so a reader can navigate back to any
part. Nest one level at most.

1. **Setting up the problem** (00:00–06:30)
   - Why the obvious approach breaks
2. **The mechanism** (06:30–19:45)

## Core concepts
The three to six ideas that carry the material. Each one explained plainly enough
that someone could restate it without the jargon — then the jargon, so they can
read the literature.

### Concept name (08:12)
**Plainly:** the idea in two or three sentences, no terminology.
**Precisely:** the same idea in the source's own terms.
**Why it matters:** what it buys you, or what goes wrong without it.

## Check yourself
Eight to fifteen active-recall questions, ordered easy to hard. Questions only —
the answers live in the next section, so the reader can genuinely attempt them.

Mix the types: recall ("what is X?"), application ("given Y, what happens?"), and
at least two that require judgment ("when would you *not* use X, and why?").

1. Why does X fail when the input is unsorted?

## Answers
Numbered to match, each with a locator back to the source.

1. Because X assumes ordering to skip the second pass. (11:05)

## Go deeper
Three to five things to study next, drawn from what the source gestures at without
covering. Name the topic and say why it follows from this material — do not invent
links, titles, or authors the source never mentions.
````

## Rules

- **Questions before answers, always.** A guide that shows the answer alongside
  the question is a summary with extra steps; the separation is what makes recall
  active.
- **Locate answers.** Every answer cites where the source supports it.
- **Mark your own synthesis `*inference*`.** The outline and each concept's
  grouping are your structure on top of the source; where a count, category, or
  principle list is yours rather than the source's own, say `*inference*` and
  point back to the passage it comes from.
- **Quotes are verbatim and contiguous.** Never splice separate passages into
  one quote; trim only with an ellipsis, and match the timestamp to the line the
  quote actually comes from.
- **If `slides.<slug>.md` exists beside the source, read it.** Cite slide
  content as `(slide mm:ss)` beside a transcript locator, prefer its spelling of
  names over garbled captions, and note where narration and slides disagree.
  Frame chrome (sponsor bands, event logos) isn't slide content.
- **Plain language first.** If a concept can only be stated in the source's
  jargon, the guide has not done its job.
- **Scale to the source.** A ten-minute video gets three concepts and eight
  questions, not the maximum of everything.
- **Never invent references.** "Go deeper" names topics, not fabricated sources.
