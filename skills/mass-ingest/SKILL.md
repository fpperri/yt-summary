---
name: yt-summary:mass-ingest
description: Sweep a list of YouTube channels for recent uploads and ingest whatever is missing. Use when the user wants to poll channels, catch up on subscriptions, batch-ingest a channel or playlist, or run a recurring "what's new" pass. For one video use yt-summary:ingest.
---

# Sweep channels → ingest what's new

Poll every channel in a manifest, work out which recent uploads aren't saved yet,
and ingest those. One command instead of per-video babysitting.

## The manifest is the user's file, not the repo's

The manifest is a path the user points at. A real subscription list is personal
data and does not belong in this repo — `examples/channels.example.txt` documents
the format with fictional channels, and the default working locations
(`channels.txt`, `subscriptions.txt`) are gitignored.

Format: one channel per line, `#` for comments. `@handle`, a full channel URL, or
a URL with a `/videos` or `/shorts` tab all work and are deduplicated to the same
channel.

**Use the `@handle`, not the channel's display name.** They are frequently
different — a channel displayed as "Some Physics Show" may live at
`@its-creators-name` — and the display name just 404s. The reliable way to get it
is to open any video from the channel and copy the handle out of its URL.

```text
# Channels I follow
@examplechannel
https://www.youtube.com/@another-example    # trailing notes are fine
```

## Workflow

### 1. Show the user what would happen — before fetching anything

```bash
python {plugin_dir}/scripts/sweep.py <manifest> --output-dir <dir> --since-days 7 --dry-run
```

`{plugin_dir}` is the plugin's installation directory. This polls the channels,
applies the date window, drops anything already ingested, and lists the rest
without fetching. **Always dry-run first and confirm the count with the user** —
a manifest of a dozen channels can resolve to hundreds of videos.

### 2. Run the sweep

```bash
python {plugin_dir}/scripts/sweep.py <manifest> --output-dir <dir> --since-days 7
```

It ingests each missing video via the same engine as `yt-summary:ingest`, pacing
requests and backing off when throttled. Long sweeps are meant to be left alone —
they can take hours if YouTube starts rate-limiting.

Useful flags: `--limit-per-channel N` to cap each channel, `--pace N` for seconds
between fetches (default 8), `--log FILE` to keep a run log, `--no-api` to skip
the API endpoint once it starts blocking, `--max-listed N` to cap how many
videos are listed per channel tab (default 100, `0` = unlimited).

### 3. Report and hand off

Report ingested / skipped / failed counts and anything worth a retry. Exit code
is `0` on a clean run, `1` if a video or a whole channel failed and nothing was
ingested, `2` if the run — or a whole channel's walk — gave up rate-limited
without ingesting anything.

To analyze what came in, run `yt-summary:digest` over the new transcripts —
those are independent per video and can be fanned out in parallel.

## Behavior worth knowing

These are the failure modes this sweep is built around; don't reimplement it by
hand and rediscover them:

- **Idempotent.** Videos already ingested under the output directory are filtered
  out by video id before any fetch. A second sweep over unchanged channels does
  nothing, so interrupting one is free — rerun it to resume.
- **Deduped two ways.** The same channel written three ways in one manifest is
  polled once; a video listed in two channels' tabs is ingested once.
- **Shorts are included.** They never appear under `/videos`, so both tabs are
  walked.
- **The RSS feed only carries ~15 entries.** It's the cheap source of upload
  dates, but a channel that posted more than that inside the window overflows it.
  Anything RSS missed gets dated individually, walking newest-first and stopping
  after a run of out-of-window videos (`--stop-after-old`, default 5) so a
  long-running channel costs a handful of probes rather than one per video.
- **Rate limits are expected.** A throttled video is retried after an exponential
  backoff (up to ~1h) rather than abandoned; the run stops only after
  `--max-block` consecutive blocks. A channel walk that gives up rate-limited
  is logged as `CHANNEL-FAIL <url> (rate limited): <msg>` and counted so a
  rate-limited run can still exit `2` even though nothing else failed.
- **A capped listing near the window edge is logged, not silently truncated.**
  If a channel tab's listing was cut off at `--max-listed` and the oldest video
  it did list is still inside the date window, videos may have been dropped —
  logged as `CAP-HIT <channel> /<tab>: listing stopped at <N> with its oldest
  video still in the window — raise --max-listed (0 = unlimited)`.
- **Unaired premieres are skipped, not failed.** They have no captions until they
  air, and the next sweep picks them up.
- **Re-uploads don't overwrite.** A video that slugs identically to an existing
  one gets its video id appended.

## Listing a single channel

To inspect or filter one channel without sweeping a manifest:

```bash
python {plugin_dir}/scripts/channel_videos.py "<channel URL or @handle>" --since-days 30 [--limit N]
```

Returns JSON `{video_id, title, channel, url, tab, published?}`. If the user wants videos
*about* a topic, filter the returned **titles** yourself — deliberately not done
in the script, so relevance judgment stays with you and no transcript is fetched
just to decide it isn't relevant.

## Bundled resources

- `scripts/sweep.py` — manifest sweep: poll, dedupe, ingest, back off
- `scripts/channel_videos.py` — list one channel's videos, date-windowed
- `examples/channels.example.txt` — the manifest format
