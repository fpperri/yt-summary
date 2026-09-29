#!/usr/bin/env python3
"""Ingest one YouTube video into ``<output-dir>/<slug>/transcript.<slug>.md``.

Writes a timestamped transcript with metadata frontmatter and prints
``SLUG=<slug>`` so the caller knows which folder was written.

Resumable: a video already ingested under the output directory is skipped rather
than refetched, so reruns are cheap and a sweep can be interrupted freely.

Usage:
    python ingest_video.py "<URL or VIDEO_ID>" [--output-dir DIR] [--force]
"""
import argparse
import datetime
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yt_transcript as yt  # noqa: E402
from slug import slugify  # noqa: E402

DEFAULT_OUTPUT_DIR = "output"

_VIDEO_ID_LINE = re.compile(r"^video_id:\s*\"?([A-Za-z0-9_-]{11})\"?\s*$", re.M)


def fetch_metadata_oembed(video_id: str) -> tuple[str, str]:
    """(title, channel) from YouTube's oembed endpoint. It carries no upload
    date, which is why it is the fallback rather than the primary.

    Does no certificate handling, same as the rest of this plugin: urllib's
    default context already honors SSL_CERT_FILE where a proxy makes that
    necessary."""
    import json
    import urllib.parse
    import urllib.request
    watch = f"https://www.youtube.com/watch?v={video_id}"
    url = "https://www.youtube.com/oembed?url=%s&format=json" % urllib.parse.quote(watch, safe="")
    with urllib.request.urlopen(url, timeout=30) as fh:
        data = json.load(fh)
    return (data.get("title") or "").strip(), (data.get("author_name") or "").strip()


def fetch_metadata(video_id: str) -> tuple[str, str, str]:
    """(title, channel, upload_date); empty strings for whatever is unavailable.

    Channel and title decide the slug, so losing them does not cost a field --
    it files the video under its own id where nobody will find it again. That is
    worth a second attempt by a different route when the first route fails.
    oembed carries no upload date, so it only ever fills whichever of title/
    channel yt-dlp came back without -- never overwrites a field yt-dlp did
    resolve.

    --skip-download still makes yt-dlp pick a format, which fails outright when
    YouTube withholds formats for this client; --ignore-no-formats-error keeps
    that from blanking title and channel too.
    """
    cmd = [
        "--skip-download", "--ignore-no-formats-error", "--no-warnings",
        "--print", "%(title)s\t%(channel)s\t%(upload_date)s",
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    title = channel = upload_date = ""
    try:
        result = yt.run_ytdlp(cmd)
        if result.returncode == 0:
            fields = (result.stdout.strip().split("\t") + ["", "", ""])[:3]
            title, channel, upload_date = ("" if f == "NA" else f.strip() for f in fields)
    except Exception:
        pass

    if not (title and channel):
        try:
            o_title, o_channel = fetch_metadata_oembed(video_id)
        except Exception:
            o_title, o_channel = "", ""
        title = title or o_title
        channel = channel or o_channel

    return title, channel, upload_date


def transcript_video_id(path) -> str | None:
    """The video id recorded in a transcript file's frontmatter, if readable."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(2048)
    except OSError:
        return None
    match = _VIDEO_ID_LINE.search(head)
    return match.group(1) if match else None


def ingested_videos(output_dir) -> dict[str, Path]:
    """Every video id already ingested under ``output_dir``, mapped to its folder.

    This is what makes a sweep idempotent — it is keyed on the id in the
    frontmatter, not on folder names, so a renamed folder never causes a refetch.
    ``ingest`` looks a video up here before fetching any metadata; a sweep passes
    its own precomputed map so each call doesn't rescan the output directory.
    """
    found = {}
    for path in Path(output_dir).glob("*/transcript.*.md"):
        vid = transcript_video_id(path)
        if vid:
            found[vid] = path.parent
    return found


def resolve_target(output_dir, slug: str, video_id: str) -> tuple[Path, bool]:
    """Pick the folder for this video and say whether it is already ingested.

    Two different videos can slug identically (a re-upload, or two channels using
    the same title). Rather than let the second silently overwrite the first, the
    colliding one gets its video id appended.
    """
    base = Path(output_dir) / slug
    existing = base / f"transcript.{slug}.md"

    if existing.exists():
        if transcript_video_id(existing) == video_id:
            return base, True
        disambiguated = f"{slug}-{video_id}"
        return Path(output_dir) / disambiguated, (
            Path(output_dir) / disambiguated / f"transcript.{disambiguated}.md"
        ).exists()

    return base, False


def _yaml_scalar(value: str) -> str:
    """Quote a frontmatter value. Video titles routinely contain ':' and '#',
    which would otherwise produce a file whose YAML does not parse."""
    value = (value or "").replace("\\", "\\\\").replace('"', '\\"')
    value = value.replace("\n", " ").strip()
    return f'"{value}"'


_FRONTMATTER_FIELD = re.compile(r"^(title|channel|published):\s*(.*)$", re.M)


def _unquote_yaml_scalar(raw: str) -> str:
    """Reverse ``_yaml_scalar``: strip its wrapping quotes and undo the
    backslash/quote escaping it applied, so a value round-trips through a
    written transcript file instead of coming back with literal backslashes."""
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        raw = raw[1:-1]
    return raw.replace('\\"', '"').replace("\\\\", "\\")


def _existing_metadata(path) -> dict[str, str]:
    """title/channel/published already recorded in a transcript file's
    frontmatter, so a --force refetch that comes back empty-handed for a
    field has something to fall back to rather than blanking it."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
    except OSError:
        return {}
    return {name: _unquote_yaml_scalar(raw.strip())
            for name, raw in _FRONTMATTER_FIELD.findall(head)}


def _find_existing_transcript(folder: Path, video_id: str) -> Path | None:
    """The transcript file already living in ``folder`` whose frontmatter
    records this id -- however the folder itself came to be renamed, since a
    rename is explicitly supported and must not spawn a second transcript."""
    for path in folder.glob("transcript.*.md"):
        if transcript_video_id(path) == video_id:
            return path
    return None


def build_document(video_id, title, channel, published, retrieved, method, segments) -> str:
    frontmatter = [
        "---",
        f"source: https://www.youtube.com/watch?v={video_id}",
        "type: transcript",
        f'video_id: "{video_id}"',
        f"title: {_yaml_scalar(title)}",
        f"channel: {_yaml_scalar(channel)}",
        f"published: {published}",
        f"retrieved: {retrieved}",
        f"fetched_via: {method}",
        "---",
        "",
    ]
    body = yt.render(segments, with_timestamps=True)
    heading = title or video_id
    return "\n".join(frontmatter) + f"# {heading}\n\n" + body + "\n"


def ingest(source: str, output_dir=DEFAULT_OUTPUT_DIR, force: bool = False,
           hints: dict | None = None, ingested: dict[str, Path] | None = None) -> dict:
    video_id = yt.extract_video_id(source)
    hints = hints or {}

    if ingested is None:
        ingested = ingested_videos(output_dir)

    existing_folder = ingested.get(video_id)
    if existing_folder is not None and not force:
        return {"slug": existing_folder.name, "video_id": video_id,
                "path": str(existing_folder), "skipped": True}

    title = hints.get("title") or ""
    channel = hints.get("channel") or ""
    published = hints.get("published") or ""

    if not (title and channel):
        m_title, m_channel, upload_date = fetch_metadata(video_id)
        title = title or m_title
        channel = channel or m_channel
        if not published and len(upload_date) == 8:
            published = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"

    existing_file = None
    if existing_folder is not None:
        existing_file = _find_existing_transcript(existing_folder, video_id)
        if existing_file is not None:
            prior = _existing_metadata(existing_file)
            title = title or prior.get("title", "")
            channel = channel or prior.get("channel", "")
            published = published or prior.get("published", "")

    fallback_slug = not (title or channel)
    slug = slugify(channel, title, fallback=video_id)

    if existing_folder is not None:
        # force is set and this id is already ingested: land back in its own
        # folder rather than letting a changed title reslug it into a second one.
        target, already = existing_folder, True
        slug = existing_file.stem[len("transcript."):] if existing_file is not None else target.name
    else:
        target, already = resolve_target(output_dir, slug, video_id)
        slug = target.name

    if already and not force:
        return {"slug": slug, "video_id": video_id, "path": str(target), "skipped": True}

    segments, method = yt.fetch_segments(video_id)

    target.mkdir(parents=True, exist_ok=True)
    document = build_document(
        video_id, title, channel, published,
        datetime.date.today().isoformat(), method, segments,
    )
    destination = existing_file if existing_file is not None else target / f"transcript.{slug}.md"
    destination.write_text(document, encoding="utf-8")

    return {
        "slug": slug, "video_id": video_id, "path": str(target),
        "skipped": False, "method": method,
        "segments": len(segments), "title": title,
        "fallback_slug": fallback_slug,
    }


def main():
    yt.utf8_stdio()

    parser = argparse.ArgumentParser(description="Ingest a YouTube video's transcript")
    parser.add_argument("source", help="YouTube URL or video ID")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR,
                        help=f"Directory to write the source set into (default: {DEFAULT_OUTPUT_DIR}/)")
    parser.add_argument("--force", action="store_true",
                        help="Refetch even if this video is already ingested")
    args = parser.parse_args()

    try:
        result = ingest(args.source, args.output_dir, args.force)
    except Exception as exc:
        print(f"FAIL {args.source}: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"SLUG={result['slug']}")
    if result["skipped"]:
        print(f"SKIP {result['video_id']} :: already ingested at {result['path']}")
    else:
        print(
            f"OK {result['video_id']} [{result['method']}] "
            f"{result['segments']} segments :: {result['title'] or '(no title)'}"
        )
        if result["fallback_slug"]:
            print(
                f"WARN {result['video_id']}: no title or channel resolved; "
                f"filed under {result['slug']}. Delete that folder and rerun "
                "once metadata resolves (--force rewrites it in place).",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()
