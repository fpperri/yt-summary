# yt-summary — a Claude Code plugin for YouTube

Fetch YouTube transcripts, save them as clean source sets, turn them into study
material, and sweep whole channels for new uploads — from inside Claude Code.

## Skills

| Skill | What it does |
|---|---|
| `ingest` | Fetch one video's transcript and save it to a directory as `transcript.<slug>.md`. |
| `digest` | Turn a saved transcript (or any text) into an analysis and a study guide beside it. |
| `mass-ingest` | Sweep a manifest of channels and ingest whatever is new. |
| `transcript` | Print a video's full transcript with timestamps. |
| `summarize` | Summarize a video into key points and takeaways. |
| `ask` | Find where a topic was discussed, with timestamped links. |
| `setup` | Install the Python dependencies. |

Skills trigger from natural language — share a URL and say what you want. All of
them accept a full URL, a `youtu.be` short link, a `/shorts/` or `/embed/` link,
or a bare 11-character video ID.

`ingest` → `digest` is the main pipeline: fetch once, then analyze what you saved
as many times as you like without refetching.

## Install

Requires Python 3.10+ and Claude Code.

```bash
/plugin marketplace add fpperri/fpp-yt-summary
```

Then install the Python dependencies — ask Claude to run the `setup` skill, or:

```bash
pip install -r requirements.txt
```

| Dependency | Why |
|---|---|
| `youtube-transcript-api` | primary transcript source |
| `yt-dlp` | fallback transcript source; title/channel/date and channel listings |
| `curl_cffi` *(optional)* | enables `yt-dlp --impersonate`, which helps past caption-endpoint throttling |

## Direct script usage

Every skill is a thin wrapper over these; they run standalone.

```bash
# Print a transcript
python scripts/yt_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID" --timestamps

# Save one video as a source set (resumable — re-running skips it)
python scripts/ingest_video.py "https://www.youtube.com/watch?v=VIDEO_ID" --output-dir output

# List a channel's recent videos as JSON
python scripts/channel_videos.py "@somechannel" --since-days 30

# Sweep a channel manifest — always dry-run first
python scripts/sweep.py my-channels.txt --output-dir output --since-days 7 --dry-run
python scripts/sweep.py my-channels.txt --output-dir output --since-days 7
```

`sweep.py` exits `0` on a clean run, `1` if a video or a whole channel failed and
nothing was ingested, `2` if the run — or a whole channel's walk — gave up
rate-limited without ingesting anything; see the comment above `exit_code` in
`scripts/sweep.py` for the exact rule. Both `sweep.py` and `channel_videos.py`
take `--max-listed` to cap how many videos are listed per channel tab (`0` =
unlimited; default 100 once a date window is in play; see `scripts/sweep.py
--help` / `scripts/channel_videos.py --help`).

An ingested video lands as:

```
output/<channel>-<title>/transcript.<channel>-<title>.md
```

with `source`, `video_id`, `title`, `channel`, `published`, `retrieved` and
`fetched_via` frontmatter. `digest` adds `analysis.<slug>.md` and
`study-guide.<slug>.md` alongside it.

## Channel manifests

`mass-ingest` reads a manifest **you** point it at — one channel per line, `#` for
comments. See [`examples/channels.example.txt`](examples/channels.example.txt).

Your real subscription list is personal data and shouldn't be committed;
`channels.txt` and `subscriptions.txt` are gitignored so the obvious working
locations are safe by default.

## Notes on fetching

- **Resumable everywhere.** Ingests are keyed on the video id recorded in the
  transcript frontmatter, so re-running an ingest or interrupting a sweep costs
  nothing. `--force` refetches deliberately.
- **Rate limits.** YouTube throttles caption endpoints per IP (`429` /
  `IpBlocked`). `sweep.py` paces requests and backs off rather than hammering.
  If you're already blocked, wait it out — retrying keeps the block hot.
  `YT_NO_API=1` skips the API endpoint, which is usually the first to block.
- **Behind a TLS-inspecting proxy?** (corporate MITM, some AV suites — the symptom
  is `CERTIFICATE_VERIFY_FAILED` or `curl: (60)`.) Fetching crosses several code
  paths and they do **not** share a trust store, so a fix that resolves one can
  leave the others failing:

  | Path | Verifies against | Honors |
  |---|---|---|
  | `youtube-transcript-api` | requests → certifi | `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` |
  | `yt-dlp` metadata + channel listings | urllib → certifi | neither |
  | `yt-dlp` caption download | curl_cffi → libcurl | `CURL_CA_BUNDLE` |
  | channel RSS feed, oembed metadata fallback | urllib → OS/stdlib trust store | `SSL_CERT_FILE`; on Windows, the system certificate store |

  Appending your proxy's root CA to the **certifi** bundle — `python -m certifi`
  prints its path — covers the first three. The fourth (plain `urllib`, used for
  RSS dates and the oembed fallback) verifies against your OS's own certificate
  store instead, so it needs the CA installed there, or `SSL_CERT_FILE` pointed
  at a bundle that includes it. Alternatively, set all three env vars above, and
  add `--compat-options no-certifi` to your yt-dlp user config
  (`%APPDATA%\yt-dlp\config` on Windows, `~/.config/yt-dlp/config` elsewhere) so
  its metadata calls fall back to the OS trust store too.

  The plugin does no certificate handling of its own and never disables
  verification — don't reach for `--no-check-certificates`, which turns the
  interception you're diagnosing into one you can no longer detect.

## Development

```bash
python -m pytest tests/ -q
```

Tests are offline: network paths are stubbed, so the suite covers URL parsing,
VTT handling, slug collisions, date windowing, and sweep behavior without
touching YouTube.
