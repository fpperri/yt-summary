#!/usr/bin/env python3
"""Fetch a YouTube transcript as clean, timestamped text.

The single transcript fetcher for this plugin. Two sources, tried in order:

1. ``youtube_transcript_api`` — clean, already-segmented captions.
2. ``yt-dlp`` auto-subs (WebVTT) — slower, but survives the cases where the API
   endpoint is blocked or the video only has auto-generated captions. Rolling
   captions are de-duplicated on the way in.

Both paths return ``(start_seconds, text)`` pairs so callers never have to care
which one answered.

Behind a TLS-inspecting proxy (corporate MITM, some AV suites) the paths below do
not share a trust store, so a fix for one can leave the others failing:

* ``youtube_transcript_api`` honors ``SSL_CERT_FILE`` / ``REQUESTS_CA_BUNDLE``.
* ``yt-dlp``'s metadata and listing calls honor neither; they need
  ``--compat-options no-certifi`` in the yt-dlp user config.
* ``yt-dlp``'s caption download runs through curl_cffi and honors
  ``CURL_CA_BUNDLE``.

Those three default to the certifi bundle, so appending the proxy's root CA to it
covers them in one step. A fourth path — the plain ``urllib`` calls used
elsewhere in this plugin for the channel RSS feed and the oembed metadata
fallback — is not certifi-based: it uses the OS/stdlib trust store, which
honors ``SSL_CERT_FILE`` and, on Windows, loads the system certificate store.
The proxy's root CA needs to land there too (or in ``SSL_CERT_FILE``) for those
calls to verify. This script does no certificate handling of its own and never
disables verification.

Usage:
    python yt_transcript.py <URL or VIDEO_ID> [--timestamps] [--delay SECONDS]
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse, parse_qs

STATE_FILE = Path(__file__).parent / "output" / ".last_video"

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_URL_VIDEO_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/|/v/)([A-Za-z0-9_-]{11})")

DEFAULT_LANGUAGES = ["en", "en-US", "en-GB"]

_IMPERSONATE_TARGET = None

# Deliberately not a bare "429": the string this is matched against embeds the
# video id, and ids are 11 chars of [A-Za-z0-9_-], so one containing "429" would
# turn every permanent failure on that video (captions disabled, deleted) into a
# false rate-limit verdict — and a caller's retry path would then stall on it
# for hours. "Sign in to confirm you're not a bot" is YouTube's IP-level bot
# wall, a distinct rate-limit signal that doesn't mention 429 at all.
_RATE_LIMITED_RE = re.compile(
    r"IpBlocked|RequestBlocked|Too Many Requests|HTTP Error 429|Error 429|"
    r"429 Client Error|Sign in to confirm you.re not a bot",
    re.I,
)
_NOT_YET_AIRED_RE = re.compile(
    r"premiere|live event will begin|not yet available|hasn't started", re.I
)
_NETWORK_RE = re.compile(
    r"Unable to download|urlopen error|timed out|getaddrinfo|"
    r"Temporary failure in name resolution|Connection (reset|refused|aborted)|"
    r"Remote end closed|CERTIFICATE_VERIFY_FAILED",
    re.I,
)


class RateLimited(RuntimeError):
    """Raised when YouTube has throttled or IP-blocked a request."""


class NotYetAired(RuntimeError):
    """Raised when a video is a premiere/live event that hasn't started."""


def failure_kind(error) -> str:
    """Classify an exception or message string as one of "rate_limited",
    "not_yet_aired", "network" or "failed", in that precedence order.

    Callers (a sweep's retry/backoff loop, an ingest run) need this verdict
    from both typed exceptions raised in this module and from plain messages
    surfaced elsewhere (e.g. a caught ``CalledProcessError``), so it accepts
    either.
    """
    message = str(error)
    if isinstance(error, RateLimited) or _RATE_LIMITED_RE.search(message):
        return "rate_limited"
    if isinstance(error, NotYetAired) or _NOT_YET_AIRED_RE.search(message):
        return "not_yet_aired"
    if _NETWORK_RE.search(message):
        return "network"
    return "failed"


def save_last_video(video_id: str) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True, parents=True)
    STATE_FILE.write_text(video_id)


def load_last_video() -> str | None:
    if STATE_FILE.exists():
        return STATE_FILE.read_text().strip() or None
    return None


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds or 0))
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f"[{h:02d}:{m:02d}:{s:02d}]"
    return f"[{m:02d}:{s:02d}]"


def extract_video_id(url: str) -> str:
    """Resolve a video id from a bare id, a watch/short/embed/live URL, or a
    youtu.be link. Returns the input's last path segment if nothing matches."""
    url = (url or "").strip()

    if _VIDEO_ID_RE.match(url):
        return url

    match = _URL_VIDEO_RE.search(url)
    if match:
        return match.group(1)

    parsed = urlparse(url)

    if parsed.netloc in ("youtu.be", "www.youtu.be"):
        return parsed.path.lstrip("/").split("/")[0]

    qs = parse_qs(parsed.query)
    if "v" in qs and qs["v"]:
        return qs["v"][0]

    return parsed.path.split("/")[-1]


def run_ytdlp(args: list[str], timeout=None) -> subprocess.CompletedProcess:
    """Run yt-dlp with the encoding flags every invocation in this plugin needs.

    Without ``--encoding utf-8`` yt-dlp's piped output drops any character
    outside cp1252 on Windows — a Japanese channel name came back empty in
    testing. Never ``check=True``: callers decide what a nonzero exit means
    (a caption track can fail while another already wrote its file).
    """
    return subprocess.run(
        ["yt-dlp", "--encoding", "utf-8", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def ytdlp_failure(result: subprocess.CompletedProcess) -> RuntimeError:
    """Turn a failed yt-dlp run into a classified, but not raised, exception."""
    lines = [line for line in (result.stderr or "").splitlines() if line.strip()]
    message = lines[-1].strip() if lines else f"yt-dlp exited {result.returncode}"
    kind = failure_kind(message)
    if kind == "rate_limited":
        return RateLimited(message)
    if kind == "not_yet_aired":
        return NotYetAired(message)
    return RuntimeError(message)


def _impersonate_target() -> str:
    """An available yt-dlp impersonate target ('chrome'), or '' if none.

    Impersonation spoofs a real browser TLS fingerprint, which is the supported
    way past YouTube's anti-bot throttling on the caption endpoint. It needs
    curl_cffi installed alongside yt-dlp; absent that, yt-dlp still lists
    Chrome targets but marks each one "curl_cffi (unavailable)", and passing
    --impersonate chrome would then fail every call. Cached — the probe is a
    subprocess call.
    """
    global _IMPERSONATE_TARGET
    if _IMPERSONATE_TARGET is None:
        try:
            out = run_ytdlp(["--list-impersonate-targets"], timeout=30).stdout
            _IMPERSONATE_TARGET = (
                "chrome" if re.search(r"^Chrome\S*\s+\S+\s+curl_cffi\s*$", out, re.M) else ""
            )
        except Exception:
            _IMPERSONATE_TARGET = ""
    return _IMPERSONATE_TARGET


def via_api(video_id: str, languages=None) -> list[tuple[float, str]]:
    """Fetch via youtube_transcript_api. Falls back to any available transcript
    when none of the preferred languages exist."""
    from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound

    languages = languages or DEFAULT_LANGUAGES
    api = YouTubeTranscriptApi()

    try:
        fetched = api.fetch(video_id, languages=languages)
    except NoTranscriptFound:
        transcripts = list(api.list(video_id))
        if not transcripts:
            raise
        fetched = transcripts[0].fetch()

    segments = []
    for snippet in getattr(fetched, "snippets", fetched):
        if isinstance(snippet, dict):
            start, text = snippet.get("start"), snippet.get("text")
        else:
            start, text = getattr(snippet, "start", None), getattr(snippet, "text", None)
        text = (text or "").replace("\n", " ").strip()
        if text:
            segments.append((float(start or 0.0), text))

    if not segments:
        raise RuntimeError("empty transcript")
    return segments


def vtt_time_to_seconds(stamp: str) -> float:
    """'00:01:23.456' or '01:23.456' -> seconds."""
    parts = stamp.split(":")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def parse_vtt(text: str, repeat_gap: float = 15.0) -> list[tuple[float, str]]:
    """Parse WebVTT into (seconds, text), stripping karaoke tags and dropping the
    repeated lines that auto-generated rolling captions emit.

    A line is dropped only when the same text already appeared within
    ``repeat_gap`` seconds. Rolling captions restate the previous cue a second or
    two later, so that catches them; a speaker's twentieth "Right." minutes on,
    or a later "[Music]" marker, is genuine content and survives. Deduping
    against the whole document instead would silently delete all of it, and the
    loss is permanent in a saved source set.

    Nothing before the first cue's timing line is transcript text — a ``STYLE``
    block's selectors and declarations are ordinary non-empty lines and would
    otherwise be emitted as bogus cues at 0.0.
    """
    time_re = re.compile(r"(\d{1,2}:\d{2}(?::\d{2})?\.\d+)\s+-->")
    tag_re = re.compile(r"<[^>]+>")

    current, out, last_seen, seen_cue = 0.0, [], {}, False
    for line in text.splitlines():
        match = time_re.search(line)
        if match:
            current = vtt_time_to_seconds(match.group(1))
            seen_cue = True
            continue
        if not seen_cue:
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        cleaned = html.unescape(tag_re.sub("", line)).strip()
        if not cleaned:
            continue
        previous = last_seen.get(cleaned)
        last_seen[cleaned] = current
        if previous is not None and current - previous <= repeat_gap:
            continue
        out.append((current, cleaned))

    if not out:
        raise RuntimeError("empty vtt")
    return out


def _preferred_vtt(filenames: list[str], manual_langs=()) -> str | None:
    """Pick the best caption file yt-dlp wrote.

    A video commonly yields several (``<id>.en.vtt``, ``<id>.en-GB.vtt``,
    ``<id>.en-orig.vtt``). A filename alone doesn't say whether it's a manual
    (uploader-written) or auto (ASR) track, and manual is always better —
    ``manual_langs`` (read from yt-dlp's info.json ``subtitles`` keys) settles
    that. Within the same tier, plain ``en`` is preferred; picking by plain
    sort order would let a regional variant win, since '-' sorts before '.'.
    """
    vtts = [f for f in filenames if f.endswith(".vtt")]
    if not vtts:
        return None

    def lang_of(filename: str) -> str:
        parts = filename.split(".")
        return parts[-2] if len(parts) >= 3 else ""

    return min(
        vtts,
        key=lambda f: (lang_of(f) not in manual_langs, lang_of(f) != "en", len(f), f),
    )


def via_ytdlp(video_id: str) -> list[tuple[float, str]]:
    """Fetch auto-subs via yt-dlp and parse the resulting WebVTT."""
    workdir = tempfile.mkdtemp()
    try:
        base = os.path.join(workdir, video_id)
        cmd = [
            "--skip-download", "--write-auto-subs", "--write-subs", "--write-info-json",
            "--sub-lang", "en.*", "--sub-format", "vtt",
            "--sleep-requests", "1", "--no-warnings",
            "-o", base + ".%(ext)s",
            f"https://www.youtube.com/watch?v={video_id}",
        ]
        target = _impersonate_target()
        if target:
            cmd[0:0] = ["--impersonate", target]

        result = run_ytdlp(cmd)

        manual_langs = ()
        try:
            with open(base + ".info.json", encoding="utf-8") as fh:
                manual_langs = json.load(fh).get("subtitles") or {}
        except (OSError, ValueError):
            pass

        # yt-dlp exits nonzero when any one of several requested tracks fails
        # (a 429 on a translation) even though the wanted <id>.en.vtt is
        # already on disk, so a written vtt wins regardless of the exit code.
        chosen = _preferred_vtt(os.listdir(workdir), manual_langs)
        if not chosen:
            if result.returncode != 0:
                raise ytdlp_failure(result)
            raise RuntimeError("no vtt written")
        with open(os.path.join(workdir, chosen), encoding="utf-8", errors="replace") as fh:
            return parse_vtt(fh.read())
    finally:
        # A bulk sweep runs this hundreds of times; without cleanup each fetch
        # strands a directory and its caption file in the system temp dir.
        shutil.rmtree(workdir, ignore_errors=True)


def fetch_segments(video_id: str, languages=None) -> tuple[list[tuple[float, str]], str]:
    """Fetch a transcript, returning (segments, method).

    Set ``YT_NO_API=1`` to skip the API and go straight to yt-dlp — worth doing in
    bulk runs once the API endpoint is throttled, since a rejected call is still a
    request that keeps the rate limit hot.

    When both sources fail, the exception raised is typed by whichever source
    saw the more actionable signal — a caller (a sweep's retry loop) needs to
    tell "throttled, back off and retry" and "premiere, try again later" apart
    from a plain failure, and either source's error can carry that signal even
    when the other's doesn't.
    """
    if os.environ.get("YT_NO_API"):
        return via_ytdlp(video_id), "ytdlp"
    try:
        return via_api(video_id, languages), "api"
    except Exception as api_error:
        try:
            return via_ytdlp(video_id), "ytdlp"
        except Exception as ytdlp_error:
            message = (
                f"transcript unavailable for {video_id}: "
                f"api={api_error!r} ytdlp={ytdlp_error!r}"
            )
            kinds = {failure_kind(api_error), failure_kind(ytdlp_error)}
            if "rate_limited" in kinds:
                raise RateLimited(message) from ytdlp_error
            if "not_yet_aired" in kinds:
                raise NotYetAired(message) from ytdlp_error
            raise RuntimeError(message) from ytdlp_error


def render(segments, with_timestamps: bool = False) -> str:
    if with_timestamps:
        return "\n".join(f"{format_timestamp(start)} {text}" for start, text in segments)
    return " ".join(text for _, text in segments)


def get_transcript(video_id: str, with_timestamps: bool = False, lang_priority=None) -> str:
    segments, _ = fetch_segments(video_id, lang_priority)
    return render(segments, with_timestamps)


def main():
    import argparse
    import time

    # A piped stdout defaults to cp1252 on Windows; a transcript containing
    # non-cp1252 characters would otherwise crash print() with a
    # UnicodeEncodeError instead of just printing the text.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Fetch a YouTube transcript")
    parser.add_argument("url", help="YouTube URL or video ID")
    parser.add_argument("--timestamps", action="store_true", help="Include timestamps in output")
    parser.add_argument("--delay", type=float, default=0,
                        help="Seconds to wait before fetching (for batch rate limiting)")
    args = parser.parse_args()

    video_id = extract_video_id(args.url)
    save_last_video(video_id)

    if args.delay > 0:
        time.sleep(args.delay)

    try:
        segments, _ = fetch_segments(video_id)
    except Exception as exc:
        print(f"Could not fetch transcript: {exc}", file=sys.stderr)
        sys.exit(2)

    print(render(segments, with_timestamps=args.timestamps))


if __name__ == "__main__":
    main()
