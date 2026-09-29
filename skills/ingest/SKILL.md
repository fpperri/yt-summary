---
name: yt-summary:ingest
description: Fetch a YouTube video's transcript and save it as a clean, timestamped source set in a directory of your choosing. Use when the user shares a YouTube URL and wants it saved, ingested, archived, or set up for study rather than just printed. For a whole channel use yt-summary:mass-ingest; to analyze something already saved use yt-summary:digest.
---

# Ingest a video → saved transcript

Fetch one video's transcript and write it, with metadata, to
`<output-dir>/<slug>/transcript.<slug>.md`. The slug is `<channel>-<title>`
kebab-cased.

This is the fetching half of the plugin. Pair it with `yt-summary:digest` to turn
the saved transcript into an analysis and study guide.

## Workflow

### 1. Fetch — use the script, always

```bash
python {plugin_dir}/scripts/ingest_video.py "<URL or VIDEO_ID>" --output-dir <dir>
```

`{plugin_dir}` is the plugin's installation directory (the one containing
`scripts/ingest_video.py`). `--output-dir` defaults to `output/` under the current
working directory — ask the user where they want it if it matters.

The script resolves the video id from any YouTube URL form, fetches the
transcript, pulls title/channel/date, computes the slug, and writes the file. It
prints:

- `SLUG=<slug>` — the folder and filename stem. Capture it; downstream steps need it.
- `OK <id> [api|ytdlp] <n> segments :: <title>` — on a successful fetch.
- `SKIP <id> :: already ingested at <path>` — the video was already there.
- `WARN <id>: no title or channel resolved; filed under <slug>. Delete that
  folder and rerun once metadata resolves (--force rewrites it in place).` —
  on stderr, when the video was saved but `yt-dlp` couldn't resolve its title
  or channel, so it landed under a video-id slug instead. Tell the user; once
  metadata is resolvable, deleting the folder and rerunning gets it a proper
  slug. `--force` does not: it rewrites the transcript in the existing folder.

Do **not** hand-scrape the YouTube page or use WebFetch for transcripts. The
script exists because raw fetching hits rolling-caption duplication and the
caption endpoint's throttling; a hand-rolled fetch gets both wrong.

### 2. Report

Tell the user the path written and the title. If they wanted analysis or study
material, hand off to `yt-summary:digest` with that path.

## Behavior worth knowing

- **Resumable.** A video already ingested under the output directory is skipped,
  matched on the video id in the transcript's frontmatter rather than on folder
  names. Re-running costs nothing. Pass `--force` to refetch deliberately.
- **`--force` doesn't lose what's already there.** It rewrites the existing
  transcript file in place — found by the video id in its frontmatter, so a
  folder the user renamed since first ingest still gets its one file
  overwritten instead of a second one alongside it. If the refetch can't
  resolve title/channel/date, the prior metadata is kept rather than blanked
  out.
- **Two transcript sources.** `youtube_transcript_api` first, falling back to
  `yt-dlp` auto-subs. The frontmatter's `fetched_via` records which answered.
- **Slug collisions don't overwrite.** If a different video already owns the slug
  (a re-upload, or two channels using one title), the new one gets its video id
  appended instead of landing on top of the existing transcript.
- **Missing metadata is survivable.** If `yt-dlp` can't resolve title/channel, the
  slug falls back to the video id — the transcript is still saved.

## Multiple videos

Treat each URL as an independent unit and fetch them one at a time, a couple of
seconds apart. Bursts trip YouTube's per-IP rate limit. For more than a handful,
or for anything channel-shaped, use `yt-summary:mass-ingest` — it paces and backs
off on its own.

## When a fetch fails

- **`429` / `IpBlocked`** — YouTube is rate-limiting this IP. Wait it out; short
  cooldowns often aren't enough and retrying keeps the block hot. Setting
  `YT_NO_API=1` skips the API endpoint (usually the first to block) and goes
  straight to `yt-dlp`.
- **No transcript / transcripts disabled** — the video has no captions in any
  language. Nothing to do; report it and move on.
- **An unaired premiere** — it has no captions until it airs. Try again later.
- **TLS interception** (corporate proxy, some AV suites) — `CERTIFICATE_VERIFY_FAILED`
  or `curl: (60)`. Fetching crosses several paths that do **not** share a trust
  store, so fixing one can leave the others failing:

  | Path | Honors |
  |---|---|
  | `youtube_transcript_api` | `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` |
  | `yt-dlp` metadata + channel listings | neither — needs `--compat-options no-certifi` |
  | `yt-dlp` caption download (curl_cffi) | `CURL_CA_BUNDLE` |
  | channel RSS feed, oembed metadata fallback | `SSL_CERT_FILE`; on Windows, the system certificate store |

  The first three default to the **certifi** bundle, so appending the proxy's
  root CA to it (`python -m certifi` prints the path) covers them in one step.
  The fourth (plain `urllib`, used for RSS dates and the oembed fallback) checks
  the OS's own certificate store instead, so it needs the CA installed there, or
  `SSL_CERT_FILE` pointed at a bundle that includes it. The env-var route works
  for the first three too; put `--compat-options no-certifi` in the yt-dlp user
  config (`%APPDATA%\yt-dlp\config`, or `~/.config/yt-dlp/config`) so it applies
  to every call this plugin makes.

  Never disable certificate verification (`--no-check-certificates`) to get past
  this — it turns the interception you are diagnosing into one you can't detect.

## Bundled resources

- `scripts/ingest_video.py` — entry point: transcript + metadata → `transcript.<slug>.md`
- `scripts/yt_transcript.py` — the transcript fetcher (API, with yt-dlp fallback)
- `scripts/slug.py` — the `<channel>-<title>` slug
