#!/usr/bin/env python3
"""Sweep a manifest of channels and ingest whatever is missing.

Usage:
    python sweep.py <manifest> [--output-dir DIR] [--since-days N] [--limit-per-channel N]
                    [--pace SECONDS] [--max-block N] [--stop-after-old N]
                    [--probe-pace SECONDS] [--max-listed N] [--dry-run]
                    [--no-api] [--log FILE]

The manifest is a plain text file you point at — one channel per line, ``#`` for
comments. It is an argument rather than a file in this repo because a real
subscription list is personal data; see ``examples/channels.example.txt`` for the
format.

The sweep is idempotent. Videos already ingested under the output directory are
filtered out by video id before anything is fetched, so re-running after an
interruption picks up exactly what is left.

Rate limiting is the main hazard on a large sweep: YouTube throttles the caption
endpoints per IP (``HTTP 429`` / ``IpBlocked``). Requests are paced, a throttled
video is retried after an exponential backoff, and the run gives up only after a
long run of consecutive blocks — so it can be left to grind rather than babysat.

A definitive failure ("failed" or "network" in ``yt_transcript.failure_kind``
terms) gets one immediate retry, after the normal pace, before it is counted
lost — a fair few of these clear on the second attempt. A rate-limited or
not-yet-aired verdict is untouched by this: those already have their own
handling (the backoff loop, and the next-sweep pickup) and are never retried
here.

Exit code: 2 if rate limiting cut the run short and work remains — the ingest
loop gave up, or any channel walk came back blocked — regardless of how many
videos were ingested first; else 1 if a video or a whole channel failed and
nothing was ingested; else 0.
"""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import channel_videos as cv  # noqa: E402
import yt_transcript as yt  # noqa: E402
from ingest_video import DEFAULT_OUTPUT_DIR, ingest, ingested_videos  # noqa: E402

BACKOFF = [60, 180, 420, 900, 1800, 3600]  # seconds; the last value repeats


def read_manifest(path) -> list[str]:
    """Channel references from the manifest, deduped by normalized URL.

    Dedupe is on the normalized form so ``@chan``, ``youtube.com/@chan`` and
    ``youtube.com/@chan/videos`` in the same file poll the channel once.
    """
    seen, channels = set(), []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            try:
                normalized = cv.normalize_channel_url(line)
            except ValueError:
                continue
            key = normalized.lower()
            if key not in seen:
                seen.add(key)
                channels.append(normalized)
    return channels


def log(message, logpath=None):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)
    if logpath:
        try:
            with open(logpath, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def collect_candidates(channels, since_days, limit_per_channel=None, stop_after_old=5,
                       logpath=None, probe_pace=1.0, max_listed=cv.DEFAULT_LISTING_CAP):
    """Poll each channel and return (candidates, channels_failed, channels_blocked),
    newest tab order.

    ``channels_failed`` counts channels that raised (list_channel exceptions
    propagate through ``window_channel``), came back with a probe error, or
    hit a rate-limited tab error, rather than folding any of those silently
    into "nothing to do" — a manifest that only ever hits dead or blocked
    channels should not look identical to a clean sweep. Any of the three
    that YouTube rate-limited is also counted in ``channels_blocked``, so the
    caller can tell an IP block apart from a channel that is simply gone —
    and, once one channel comes back blocked, the walk stops polling the rest
    of the manifest outright rather than keep hammering a throttled IP; the
    candidates already collected still go on to be ingested.
    """
    import datetime

    cutoff = (datetime.date.today() - datetime.timedelta(days=since_days)).isoformat()
    seen, candidates, channels_failed, channels_blocked = set(), [], 0, 0

    for position, channel in enumerate(channels):
        channel_blocked = False
        probe_error = None

        def _on_tab_error(tab, err, c=channel):
            nonlocal channel_blocked
            log(f"TAB-FAIL {c} /{tab}: {err}", logpath)
            if yt.failure_kind(err) == "rate_limited":
                channel_blocked = True

        def _on_probe_error(exc):
            nonlocal probe_error
            probe_error = exc

        try:
            videos = cv.window_channel(
                channel, cutoff, stop_after_old, probe_pace, limit_per_tab=max_listed,
                on_tab_error=_on_tab_error,
                on_rss_miss=lambda exc, c=channel: log(
                    f"RSS-MISS {c}: {exc} — dating each video individually", logpath),
                on_cap_hit=lambda tab, limit, c=channel: log(
                    f"CAP-HIT {c} /{tab}: listing stopped at {limit} without confirming the "
                    "window had ended — raise --max-listed (0 = unlimited)", logpath),
                on_probe_error=_on_probe_error,
            )
        except Exception as exc:
            channels_failed += 1
            rate_limited = yt.failure_kind(exc) == "rate_limited"
            if rate_limited:
                channels_blocked += 1
                channel_blocked = True
            suffix = " (rate limited)" if rate_limited else ""
            log(f"CHANNEL-FAIL {channel}{suffix}: {exc}", logpath)
            if channel_blocked:
                remaining = len(channels) - position - 1
                log(f"BLOCKED while polling — {remaining} channel(s) not polled; "
                    "rerun to resume", logpath)
                break
            continue

        if probe_error is not None:
            channels_failed += 1
            rate_limited = yt.failure_kind(probe_error) == "rate_limited"
            if rate_limited:
                channels_blocked += 1
                channel_blocked = True
            suffix = " (rate limited)" if rate_limited else ""
            log(f"CHANNEL-FAIL {channel}{suffix}: {probe_error} — keeping {len(videos)} "
                "video(s) already dated", logpath)
        elif channel_blocked:
            channels_failed += 1
            channels_blocked += 1

        if limit_per_channel is not None:
            videos = videos[:limit_per_channel]

        fresh = 0
        for video in videos:
            if video["video_id"] in seen:
                continue
            seen.add(video["video_id"])
            candidates.append({**video, "channel_url": channel})
            fresh += 1
        log(f"CHANNEL {channel}: {fresh} in window", logpath)

        if channel_blocked:
            remaining = len(channels) - position - 1
            log(f"BLOCKED while polling — {remaining} channel(s) not polled; "
                "rerun to resume", logpath)
            break

    return candidates, channels_failed, channels_blocked


def _ingest_once_with_retry(video_id, output_dir, hints, ingested_map, pace, logpath):
    """Call ``ingest`` once; on a "failed" or "network" verdict, wait the
    normal pace and try that same video exactly once more before letting the
    exception through. A rate-limited or not-yet-aired verdict is raised
    straight through untouched — those are handled by the caller's own
    backoff loop and next-sweep pickup, not by this retry."""
    try:
        return ingest(video_id, output_dir, hints=hints, ingested=ingested_map)
    except Exception as exc:
        if yt.failure_kind(exc) not in ("failed", "network"):
            raise
        log(f"RETRY {video_id}: {exc}", logpath)
        time.sleep(pace)
        return ingest(video_id, output_dir, hints=hints, ingested=ingested_map)


def sweep(manifest, output_dir=DEFAULT_OUTPUT_DIR, since_days=7, limit_per_channel=None,
          pace=8.0, max_block=30, dry_run=False, logpath=None, stop_after_old=5,
          probe_pace=1.0, max_listed=cv.DEFAULT_LISTING_CAP):
    channels = read_manifest(manifest)
    if not channels:
        log(f"no channels found in {manifest}", logpath)
        return {"ingested": 0, "skipped": 0, "failed": 0, "blocked": False,
                "candidates": [], "channels_failed": 0, "channels_blocked": 0}

    log(f"sweeping {len(channels)} channel(s), last {since_days} day(s)", logpath)
    candidates, channels_failed, channels_blocked = collect_candidates(
        channels, since_days, limit_per_channel, stop_after_old, logpath, probe_pace, max_listed)

    ingested_map = ingested_videos(output_dir)
    todo = [c for c in candidates if c["video_id"] not in ingested_map]
    log(f"{len(candidates)} candidate(s), {len(candidates) - len(todo)} already ingested, "
        f"{len(todo)} to fetch", logpath)

    if dry_run:
        for candidate in todo:
            log(f"WOULD INGEST {candidate['video_id']} :: {candidate['title']}", logpath)
        return {"ingested": 0, "skipped": 0, "failed": 0, "blocked": False,
                "candidates": todo, "channels_failed": channels_failed,
                "channels_blocked": channels_blocked}

    ingested = skipped = failed = 0
    consecutive_blocks = 0
    blocked = False
    index = 0

    while index < len(todo):
        candidate = todo[index]
        video_id = candidate["video_id"]
        hints = {"title": candidate.get("title", ""), "channel": candidate.get("channel", ""),
                 "published": candidate.get("published", "")}
        try:
            result = _ingest_once_with_retry(video_id, output_dir, hints, ingested_map, pace, logpath)
        except Exception as exc:
            message = str(exc)
            kind = yt.failure_kind(exc)

            if kind == "rate_limited":
                consecutive_blocks += 1
                if consecutive_blocks >= max_block:
                    blocked = True
                    log(f"BLOCKED {consecutive_blocks}x consecutively — stopping. "
                        f"{len(todo) - index} left; rerun to resume.", logpath)
                    break
                wait = BACKOFF[min(consecutive_blocks - 1, len(BACKOFF) - 1)]
                log(f"429 on {video_id} (block #{consecutive_blocks}) — waiting {wait}s, "
                    f"then retrying the same video", logpath)
                time.sleep(wait)
                continue

            consecutive_blocks = 0
            index += 1
            if kind == "not_yet_aired":
                skipped += 1
                log(f"PREMIERE {video_id} — not aired yet, will be picked up next sweep", logpath)
            else:
                failed += 1
                log(f"FAIL {video_id}: {message[:200]}", logpath)
            time.sleep(pace)
            continue

        consecutive_blocks = 0
        index += 1
        if result["skipped"]:
            skipped += 1
            log(f"SKIP {video_id} :: already at {result['path']}", logpath)
        else:
            ingested += 1
            ingested_map[video_id] = Path(result["path"])
            note = (" (fallback slug: no title/channel resolved; delete the folder and rerun "
                    "once metadata resolves)") if result.get("fallback_slug") else ""
            log(f"OK [{ingested}] {video_id} -> {result['slug']}{note}", logpath)
            time.sleep(pace)

    log(f"sweep end: ingested={ingested} skipped={skipped} failed={failed} "
        f"channels_failed={channels_failed} channels_blocked={channels_blocked}"
        f"{' (gave up: rate limited)' if blocked else ''}", logpath)
    return {"ingested": ingested, "skipped": skipped, "failed": failed,
            "blocked": blocked, "candidates": todo, "channels_failed": channels_failed,
            "channels_blocked": channels_blocked}


def exit_code(result) -> int:
    # 2 = rate limiting cut the run short and work remains — the ingest loop
    # gave up, or any channel walk was blocked — even when videos were
    # ingested before the block hit, since a scheduled wrapper still needs to
    # know the IP is blocked and channels are left un-walked; 1 flags a run
    # that produced no ingests despite a video or a whole channel failing for
    # some other reason, so a wrapper doesn't have to parse the log to notice.
    if result["blocked"] or result["channels_blocked"]:
        return 2
    if (result["failed"] or result["channels_failed"]) and not result["ingested"]:
        return 1
    return 0


def main():
    yt.utf8_stdio()

    parser = argparse.ArgumentParser(description="Sweep a channel manifest and ingest new videos")
    parser.add_argument("manifest", help="Path to the channel manifest file")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR,
                        help=f"Where source sets are written (default: {DEFAULT_OUTPUT_DIR}/)")
    parser.add_argument("--since-days", type=int, default=7,
                        help="Only consider uploads from the last N days (default: 7)")
    parser.add_argument("--limit-per-channel", type=int, default=None,
                        help="Cap candidates taken from each channel")
    parser.add_argument("--pace", type=float, default=8.0,
                        help="Seconds between fetches (default: 8)")
    parser.add_argument("--max-block", type=int, default=30,
                        help="Give up after this many consecutive rate-limit blocks")
    parser.add_argument("--stop-after-old", type=int, default=5,
                        help="Stop date-probing a channel after this many consecutive old videos")
    parser.add_argument("--probe-pace", type=float, default=1.0,
                        help="Seconds between per-video date probes while polling (default: 1)")
    parser.add_argument("--max-listed", type=cv.non_negative_int, default=cv.DEFAULT_LISTING_CAP,
                        help="Cap videos listed per channel tab, 0 = unlimited "
                             f"(default: {cv.DEFAULT_LISTING_CAP})")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be ingested and exit")
    parser.add_argument("--no-api", action="store_true",
                        help="Skip youtube_transcript_api; go straight to yt-dlp")
    parser.add_argument("--log", default=None, help="Also append run output to this file")
    args = parser.parse_args()

    if args.no_api:
        os.environ["YT_NO_API"] = "1"

    result = sweep(
        args.manifest, args.output_dir, args.since_days, args.limit_per_channel,
        args.pace, args.max_block, args.dry_run, args.log, args.stop_after_old,
        args.probe_pace, args.max_listed,
    )

    sys.exit(exit_code(result))


if __name__ == "__main__":
    main()
