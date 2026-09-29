#!/usr/bin/env python3
"""List a YouTube channel's videos as JSON candidates for ingest.

Usage:
    python channel_videos.py "<channel URL or @handle>" [--since-days N] [--limit N]
        [--stop-after-old N] [--probe-pace SECONDS] [--max-listed N]

Output (stdout): a JSON array of ``{video_id, title, channel, url, tab, published?}``.

Two details worth knowing before changing this:

* **The RSS feed only carries ~15 entries.** It is the cheap way to get upload
  dates, but on a channel that posted more than that inside the window it silently
  drops the rest. So the listing comes from ``yt-dlp --flat-playlist`` over the
  ``/videos`` *and* ``/shorts`` tabs (shorts do not appear under /videos), and RSS
  is used only to date what it happens to cover.
* **Anything RSS missed gets dated individually.** That probe costs one request
  per video, so it walks newest-first and stops once it has seen a run of
  out-of-window videos — channel tabs are roughly reverse-chronological.

Topic filtering ("videos about X") is deliberately not done here: the caller
filters the returned titles semantically. This script stays deterministic.
"""
import argparse
import datetime
import json
import os
import re
import ssl
import sys
import time
import urllib.request
from itertools import groupby
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yt_transcript as yt  # noqa: E402

try:
    from defusedxml.ElementTree import fromstring as xml_fromstring
except Exception:
    from xml.etree.ElementTree import fromstring as xml_fromstring

TABS = ("videos", "shorts")
RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
ATOM_NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
DEFAULT_LISTING_CAP = 100

# youtube.com, www.youtube.com and m.youtube.com are the same channel; a
# manifest mixing them must dedupe to one poll.
_CANONICAL_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}


def normalize_channel_url(value: str) -> str:
    """Accept '@handle', 'youtube.com/@handle', a /channel/ or /c/ URL, or a URL
    already pointing at a tab. Returns the channel base URL, tab stripped."""
    value = (value or "").strip().rstrip("/")
    if not value:
        raise ValueError("empty channel reference")

    if value.startswith("@"):
        value = f"https://www.youtube.com/{value}"
    elif not value.startswith(("http://", "https://")):
        value = "https://" + value

    parts = urlsplit(value)
    if parts.netloc.lower() in _CANONICAL_HOSTS:
        value = urlunsplit(parts._replace(scheme="https", netloc="www.youtube.com"))

    # Strip a trailing tab only when something identifying the channel precedes
    # it. Without that guard a channel literally handled "@videos" loses its own
    # handle and we end up requesting https://www.youtube.com/videos.
    for tab in TABS + ("streams", "featured", "playlists"):
        suffix = "/" + tab
        if value.endswith(suffix):
            stem = value[: -len(suffix)]
            if re.search(r"/(@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)$", stem):
                value = stem
            break
    return value


def flat_list(url: str, limit: int | None = None) -> tuple[list[dict], str | None]:
    """(videos, error). A failing tab is not on its own a problem — a channel
    with no shorts has no /shorts page — so the caller decides what it means.

    ``channel`` comes from ``%(playlist_channel)s`` — the per-entry
    ``%(channel)s`` is ``NA`` in a flat listing, but the top-level one is set
    for every entry on the tab; yt-dlp's own "NA" placeholder becomes "".
    The print template puts title last and this splits with ``maxsplit`` so a
    title containing a literal tab survives intact.
    """
    args = ["--flat-playlist", "--no-warnings"]
    if limit:
        args += ["--playlist-end", str(limit)]
    args += ["--print", "%(id)s\t%(playlist_channel)s\t%(title)s", url]
    result = yt.run_ytdlp(args)
    if result.returncode != 0:
        return [], str(yt.ytdlp_failure(result))

    videos = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        video_id, channel, title = (part.strip() for part in parts)
        if channel == "NA":
            channel = ""
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            videos.append({"video_id": video_id, "title": title, "channel": channel})
    return videos, None


def list_channel(base_url: str, tabs=TABS, on_tab_error=None, limit_per_tab=None,
                 on_tab_listed=None) -> list[dict]:
    """Videos across the channel's tabs, deduped by id, listing order preserved.

    Raises if *every* tab failed — that means the channel reference is wrong (a
    common one: using the channel's display name where its @handle is required),
    and reporting yt-dlp's own message beats an empty list.

    ``on_tab_error`` is called for each individual tab failure. A failing tab is
    usually benign (no /shorts page), but it can also be a transient error on
    /videos, in which case the listing silently under-reports and the sweep's
    idempotence means a later rerun never notices the gap. The caller gets told
    either way and decides whether to care.

    ``on_tab_listed`` gets each successful tab's raw video count and last
    (oldest-listed) video id *before* the cross-tab dedupe below removes
    anything — a caller deciding whether ``limit_per_tab`` actually cut a tab
    off needs the count yt-dlp returned for that tab alone, not what survived
    dedupe against every other tab.
    """
    seen, out, errors = set(), [], []
    for tab in tabs:
        videos, error = flat_list(f"{base_url}/{tab}", limit_per_tab)
        if error:
            errors.append(f"{tab}: {error}")
            if on_tab_error:
                on_tab_error(tab, error)
        elif on_tab_listed:
            on_tab_listed(tab, len(videos), videos[-1]["video_id"] if videos else None)
        for video in videos:
            if video["video_id"] in seen:
                continue
            seen.add(video["video_id"])
            out.append({**video, "tab": tab})

    if not out and errors:
        raise RuntimeError("; ".join(errors))
    return out


def channel_id(url: str) -> str:
    """The UC… id, needed to build the RSS URL. Read from a one-item flat dump —
    in flat mode the per-entry channel_id is NA, but the top-level one is set."""
    result = yt.run_ytdlp(["--flat-playlist", "--playlist-items", "1", "-J",
                          "--no-warnings", url])
    if result.returncode != 0:
        raise yt.ytdlp_failure(result)
    cid = json.loads(result.stdout).get("channel_id")
    if not cid:
        raise RuntimeError("could not resolve channel_id")
    return cid


def rss_dates(cid: str) -> dict[str, str]:
    """{video_id: 'YYYY-MM-DD'} for the ~15 most recent uploads."""
    data = urllib.request.urlopen(
        RSS_URL.format(cid), context=ssl.create_default_context(), timeout=20
    ).read()
    dates = {}
    for entry in xml_fromstring(data).findall("a:entry", ATOM_NS):
        vid = entry.find("yt:videoId", ATOM_NS)
        published = entry.find("a:published", ATOM_NS)
        if vid is not None and published is not None and published.text:
            dates[vid.text] = published.text[:10]
    return dates


def probe_upload_date(video_id: str) -> str:
    """'YYYY-MM-DD' for one video, or '' if it cannot be resolved (an unaired
    premiere, a members-only video, a deleted id).

    Raises when the failure is rate-limited or a network error: those are
    transient, and apply_date_window must not read a throttled probe as "this
    video is an undatable premiere" — that would let the walk run unpaced
    through the whole channel history and silently return nothing.
    """
    result = yt.run_ytdlp(["--skip-download", "--no-warnings",
                          "--print", "%(upload_date)s",
                          f"https://www.youtube.com/watch?v={video_id}"])
    if result.returncode != 0:
        exc = yt.ytdlp_failure(result)
        if yt.failure_kind(exc) in ("rate_limited", "network"):
            raise exc
        return ""
    raw = result.stdout.strip()
    if len(raw) != 8 or not raw.isdigit():
        return ""
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"


def apply_date_window(videos, cutoff, known_dates, stop_after_old=5, pace=0.0):
    """Keep videos published on/after ``cutoff``, dating the ones RSS missed.

    Stops probing after ``stop_after_old`` consecutive out-of-window videos, so a
    channel with a decade of uploads costs a handful of requests rather than one
    per video. Undatable videos (unaired premieres) are dropped from a windowed
    listing but do not count toward the stop, since they carry no date signal.

    **The early stop is per tab.** ``videos`` arrives grouped by tab, and every
    established channel has more than ``stop_after_old`` old uploads at the tail
    of /videos — so a counter shared across the whole list would break before
    reaching the first short and silently drop every short the channel has.

    ``pace`` seconds are slept between individual date probes. Probing is one
    request per video, and a channel whose RSS lookup failed probes all of them,
    so an unpaced walk here can provoke the rate limit the ingest phase is built
    to survive.
    """
    kept, probed = [], False

    for _tab, group in groupby(videos, key=lambda v: v.get("tab")):
        consecutive_old = 0
        for video in group:
            published = known_dates.get(video["video_id"])
            if published is None:
                if probed and pace:
                    time.sleep(pace)
                published = probe_upload_date(video["video_id"])
                probed = True

            if not published:
                continue

            if published >= cutoff:
                kept.append({**video, "published": published})
                consecutive_old = 0
            else:
                consecutive_old += 1
                if consecutive_old >= stop_after_old:
                    break  # done with this tab; the next one still gets walked

    kept.sort(key=lambda v: v["published"], reverse=True)
    return kept


def window_channel(base_url, cutoff, stop_after_old=5, probe_pace=1.0,
                   limit_per_tab=DEFAULT_LISTING_CAP, on_tab_error=None,
                   on_rss_miss=None, on_cap_hit=None) -> list[dict]:
    """List a channel and keep only what falls inside the date window — the
    list -> RSS -> date-window sequence a sweep and ``main`` both need.

    A listing or a probe failure (an exhausted rate limit, a network error)
    propagates rather than being folded into "nothing in the window", since a
    caller retrying blind on an empty result would never notice the channel
    was never actually walked. A failed RSS lookup is not that: it only makes
    dating more expensive (every video gets probed individually), so it falls
    back to an empty dict instead, and ``on_rss_miss`` lets the caller say so.

    ``on_cap_hit(tab, limit_per_tab)`` fires for a tab whose raw listing was
    cut off at ``limit_per_tab`` *and* whose oldest listed video still made it
    into the kept window — meaning videos beyond the cap could plausibly still
    be in-window too, and were never even listed. A tab that came up short of
    the cap on its own, or whose tail fell (or walked itself, via the early
    stop above) out of the window, never fires it.
    """
    tab_listings = {}

    def _note_tab(tab, raw_count, last_video_id):
        tab_listings[tab] = (raw_count, last_video_id)

    videos = list_channel(base_url, limit_per_tab=limit_per_tab, on_tab_error=on_tab_error,
                          on_tab_listed=_note_tab)
    try:
        known = rss_dates(channel_id(base_url))
    except Exception as exc:
        known = {}
        if on_rss_miss:
            on_rss_miss(exc)
    kept = apply_date_window(videos, cutoff, known, stop_after_old, probe_pace)

    if on_cap_hit and limit_per_tab:
        kept_ids = {video["video_id"] for video in kept}
        for tab, (raw_count, last_video_id) in tab_listings.items():
            if raw_count == limit_per_tab and last_video_id in kept_ids:
                on_cap_hit(tab, limit_per_tab)

    return kept


def non_negative_int(value: str) -> int:
    """argparse type for --max-listed, shared with sweep.py so both CLIs accept
    the same values."""
    try:
        parsed = int(value)
    except ValueError:
        parsed = -1
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"must be a non-negative integer: {value}")
    return parsed


def main():
    parser = argparse.ArgumentParser(description="List a channel's videos as JSON")
    parser.add_argument("channel", help="Channel URL or @handle")
    parser.add_argument("--since-days", type=int, default=None,
                        help="Keep only videos published within the last N days")
    parser.add_argument("--limit", type=int, default=None, help="Cap the result count")
    parser.add_argument("--stop-after-old", type=int, default=5,
                        help="Stop date-probing after this many consecutive out-of-window videos")
    parser.add_argument("--probe-pace", type=float, default=1.0,
                        help="Seconds between per-video date probes (default: 1)")
    parser.add_argument("--max-listed", type=non_negative_int, default=None,
                        help=f"Cap videos listed per tab, 0 = unlimited (default: {DEFAULT_LISTING_CAP} "
                             "when --since-days is given, unlimited otherwise)")
    args = parser.parse_args()

    try:
        base = normalize_channel_url(args.channel)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        sys.exit(1)

    limit_per_tab = args.max_listed
    if limit_per_tab is None and args.since_days is not None:
        limit_per_tab = DEFAULT_LISTING_CAP

    def _report_cap_hit(tab, limit):
        print(json.dumps({
            "warning": f"{tab} listing stopped at {limit} with its oldest video still in "
                       "the window; raise --max-listed (0 = unlimited)",
        }), file=sys.stderr)

    try:
        if args.since_days is not None:
            cutoff = (datetime.date.today() - datetime.timedelta(days=args.since_days)).isoformat()
            videos = window_channel(base, cutoff, args.stop_after_old, args.probe_pace,
                                    limit_per_tab=limit_per_tab, on_cap_hit=_report_cap_hit)
        else:
            videos = list_channel(base, limit_per_tab=limit_per_tab)
    except RuntimeError as exc:
        print(json.dumps({"error": f"could not list {base}", "detail": str(exc)}), file=sys.stderr)
        sys.exit(1)

    if not videos:
        print(json.dumps({"error": f"no videos listed for {base}"}), file=sys.stderr)
        sys.exit(1)

    if args.limit is not None:
        videos = videos[: args.limit]

    for video in videos:
        video["url"] = f"https://www.youtube.com/watch?v={video['video_id']}"

    print(json.dumps(videos, indent=2))


if __name__ == "__main__":
    main()
