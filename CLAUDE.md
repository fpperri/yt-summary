## Project Context
- Plugin type: Claude Code marketplace plugin for YouTube transcript/summary workflows
- Stack: Python (scripts), Markdown skills (SKILL.md pattern), pytest for tests
- Skills live in `skills/<name>/SKILL.md`

## Lab Notes
> When Claude makes a mistake that required correction, append a one-liner here.
> These are harvested by /insights-collect and cleared after capture.

- 2026-09-28: a typed-exception fix passed its isinstance test but never reached the sweep, which classifies via failure_kind(str(exc)); pin error handling through the classifier the caller actually uses.
- 2026-09-28: narrowing a failure regex (dropping bare "Unable to download") reclassified Windows socket errors (WinError 10060/10061) as permanent; test both directions with real platform error strings.

## Failure Log
> Permanent learnings promoted by /insights-apply from past lab notes.
