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

Exit code: 0 nothing to report, or a failure alongside at least one successful
ingest; 1 a video or a whole channel failed and nothing was ingested; 2 the
run itself, or a whole channel's walk, gave up rate-limited without
ingesting anything.
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
    propagate through ``window_channel``) rather than folding them silently
    into "nothing to do" — a manifest that only ever hits dead channels
    should not look identical to a clean sweep. A channel walk that raised
    because YouTube rate-limited it is also counted in ``channels_blocked``,
    so the caller can tell an IP block apart from a channel that is simply
    gone.
    """
    import datetime

    cutoff = (datetime.date.today() - datetime.timedelta(days=since_days)).isoformat()
    seen, candidates, channels_failed, channels_blocked = set(), [], 0, 0

    for channel in channels:
        try:
            videos = cv.window_channel(
                channel, cutoff, stop_after_old, probe_pace, limit_per_tab=max_listed,
                on_tab_error=lambda tab, err, c=channel: log(f"TAB-FAIL {c} /{tab}: {err}", logpath),
                on_rss_miss=lambda exc, c=channel: log(
                    f"RSS-MISS {c}: {exc} — dating each video individually", logpath),
                on_cap_hit=lambda tab, limit, c=channel: log(
                    f"CAP-HIT {c} /{tab}: listing stopped at {limit} with its oldest video "
                    "still in the window — raise --max-listed (0 = unlimited)", logpath),
            )
        except Exception as exc:
            channels_failed += 1
            rate_limited = yt.failure_kind(exc) == "rate_limited"
            if rate_limited:
                channels_blocked += 1
            suffix = " (rate limited)" if rate_limited else ""
            log(f"CHANNEL-FAIL {channel}{suffix}: {exc}", logpath)
            continue

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

    return candidates, channels_failed, channels_blocked


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
            result = ingest(video_id, output_dir, hints=hints, ingested=ingested_map)
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
            note = " (fallback slug: no title/channel resolved)" if result.get("fallback_slug") else ""
            log(f"OK [{ingested}] {video_id} -> {result['slug']}{note}", logpath)
            time.sleep(pace)

    log(f"sweep end: ingested={ingested} skipped={skipped} failed={failed}"
        f"{' (gave up: rate limited)' if blocked else ''}", logpath)
    return {"ingested": ingested, "skipped": skipped, "failed": failed,
            "blocked": blocked, "candidates": todo, "channels_failed": channels_failed,
            "channels_blocked": channels_blocked}


def exit_code(result) -> int:
    # 2 = gave up rate-limited (either the ingest loop itself or a whole
    # channel walk), so a scheduled wrapper can tell "the IP is blocked,
    # nothing was done" apart from "nothing new to fetch" (0); 1 flags a run
    # that produced no ingests despite a video or a whole channel failing for
    # some other reason, so a wrapper doesn't have to parse the log to notice.
    if result["blocked"] or (result["channels_blocked"] and not result["ingested"]):
        return 2
    if (result["failed"] or result["channels_failed"]) and not result["ingested"]:
        return 1
    return 0


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"must be non-negative: {value}")
    return parsed


def main():
    # A piped stdout defaults to cp1252 on Windows; a title or channel name
    # containing non-cp1252 characters would otherwise crash print() with a
    # UnicodeEncodeError instead of just printing it.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

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
    parser.add_argument("--max-listed", type=_non_negative_int, default=cv.DEFAULT_LISTING_CAP,
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
