#!/usr/bin/env python3
"""Capture a talk's slides beside its ingested transcript.

Deterministic half of a slide-capture workflow; the agent still does the
judging -- which frames are content, what a slide says. Standard library
only; shells out to ffmpeg, ffprobe and yt-dlp, which must already be on
PATH (this script installs nothing).

    slides.py fetch    --dir SRC [--height 1080]
    slides.py detect   --dir SRC [--crop full|aie|overlay|W,H,X,Y] [--threshold 0.04]
                       [--merge 2.5] [--settle 1.5] [--fps 2] [--sparse 180]
                       [--dup-threshold N] [--keep-dups]
    slides.py sample   --dir SRC --from TIME --to TIME [--fps 1] [--crop ...]
    slides.py layout   --dir SRC [--at T1,T2,T3,...]
    slides.py extract  --dir SRC --pick SECONDS=name [--pick ...] [--quality 82] [--crop ...]
    slides.py tile     --dir SRC [--slide IMG ...]
    slides.py add-shot --dir SRC --file IMG --at TIME --name NAME

SRC is the ingested source folder (the one holding transcript.<slug>.md); the video id
comes from that transcript's frontmatter. TIME is seconds, MM:SS or HH:MM:SS, fractions
allowed. Work files (video, frames, contact sheets) go to <OS temp>/ingest-slides/<video_id>/,
never into a repository. Chosen slides go to SRC/slides/<mmss>-<name>.webp and are never
overwritten.
"""
import argparse
import glob
import io
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yt_transcript as yt  # noqa: E402
from ingest_video import transcript_video_id  # noqa: E402

CROPS = {
    "full": None,
    # AI Engineer conference layout: slide top-right, speaker inset bottom-left, sponsor bar below.
    "aie": "iw*0.70:ih*0.62:iw*0.29:0",
    # Cards top-left over a talking head; proven on one video so far.
    "overlay": "iw*0.6:ih*0.55:0:0",
}

FONT_CANDIDATES = [
    "C:/Windows/Fonts/consola.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
]

TILE_W = 380  # keeps a 4x4 sheet near 1.5k px wide, which is what a model reads without downscaling
TILE_READ_W = 1600  # width the `tile` reading crops are upscaled to

# Perceptual-hash grab size for de-duplication. A dHash compares each pixel to
# its right neighbour, so it survives uniform dimming (an animation step that
# only darkens the frame); the resolution has to be high enough that two
# light, mostly-white text slides sharing a template but with different words
# land far apart in Hamming distance instead of colliding on "mostly white" --
# 16x16 wasn't: two such slides measured 8-9 bits apart, under the old default
# threshold of 14. 32x32 pushes the same pair past 25 while still catching a
# genuine dimmed repeat (measured ~11 bits at this size).
HASH_W, HASH_H = 32, 32
# Calibrated against slides kept by hand on seven recorded talks: 14 never hid
# a kept slide's only sample on any of them, but a template-heavy deck already
# did at 18, so the default stays conservative. Repetitive overlay layouts
# tolerate ~60 (see the skill's DUP notes).
DEFAULT_DUP_THRESHOLD = 14

WORKROOT_ENV = "SLIDES_WORKROOT"
TOOLS = {}


def die(msg):
    sys.exit(f"FAIL {msg}")


def need(tool_name):
    path = shutil.which(tool_name)
    if not path:
        die(f"{tool_name} not found on PATH -- install it yourself; this script installs nothing")
    TOOLS[tool_name] = path
    return path


def tool(name):
    return TOOLS.get(name) or need(name)


def transcript(src):
    """The video id recorded beside this source folder's transcript."""
    hits = glob.glob(os.path.join(src, "transcript.*.md"))
    if not hits:
        die(f"no transcript.*.md in {src} -- ingest the video first; slides sit beside a transcript")
    vid = transcript_video_id(hits[0])
    if not vid:
        die(f"{hits[0]} has no valid video_id in its frontmatter")
    return vid


def workroot():
    override = os.environ.get(WORKROOT_ENV)
    base = os.path.realpath(override) if override else os.path.realpath(tempfile.gettempdir())
    if not os.path.isabs(base):
        die(f"temp dir {base} is not absolute; set TEMP (or {WORKROOT_ENV}) to an absolute path")
    return base


def workdir(vid):
    """<workroot>/ingest-slides/<vid>, refusing a root that sits inside a git
    repository -- work files (a downloaded video, frames) must never land in
    one. ``SLIDES_WORKROOT`` overrides the OS temp dir, so tests never touch
    the real one or a repository."""
    base = workroot()
    d = base
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            die(f"temp dir {base} sits inside a git repository ({d}); set TEMP (or {WORKROOT_ENV}) outside it")
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    w = os.path.join(base, "ingest-slides", vid)
    os.makedirs(w, exist_ok=True)
    return w


def video(work):
    hits = [p for p in glob.glob(os.path.join(work, "video.*")) if re.search(r"video\.(mp4|webm|mkv)$", p)]
    if not hits:
        die("no downloaded video -- run `fetch` first")
    if len(hits) > 1:
        die(f"more than one video in {work}: {hits} -- delete the stale one")
    return hits[0]


def secs(ts):
    ts = str(ts).strip()
    if not ts:
        raise ValueError("empty time")
    t = 0.0
    for p in ts.split(":"):
        t = t * 60 + float(p)
    return t


def split(t):
    t = int(t)
    return t // 3600, t % 3600 // 60, t % 60


def clock(t):
    h, m, s = split(t)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def stamp(t):
    h, m, s = split(t)
    return f"{h:02d}{m:02d}{s:02d}" if h else f"{m:02d}{s:02d}"


def run(cmd, text=True):
    """Run an ffmpeg/ffprobe command. Never ``check=True``: ``grab``'s
    one-second-earlier fallback needs to see a failed attempt's return code
    rather than have it raised past before the fallback can run."""
    if text:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return subprocess.run(cmd, capture_output=True)


def crop_expr(c):
    key = (c or "full").lower()
    if key in CROPS:
        return CROPS[key]
    parts = c.split(",")
    if len(parts) != 4:
        die("--crop must be full, aie, overlay or W,H,X,Y (ffmpeg crop order; expressions like iw*0.7 allowed)")
    return ":".join(parts)


def find_font():
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def label_filter(text):
    font = find_font()
    if not font:
        return ""
    escaped_path = font.replace(":", "\\:")
    escaped_text = text.replace(":", chr(92) + ":")
    return (f",drawtext=fontfile='{escaped_path}':text='{escaped_text}':x=8:y=8:fontsize=h/14"
            ":fontcolor=white:box=1:boxcolor=black@0.75:boxborderw=6")


def safe_name(name):
    n = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
    n = re.sub(r"-{2,}", "-", n)
    if not n:
        die(f"name '{name}' has no usable characters")
    return n


def duration(v):
    p = run([tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", v])
    try:
        return float((p.stdout or "").strip())
    except ValueError:
        return 0.0


def clamp_range(t0, t1, dur, margin=0.5):
    """Clamp a [t0, t1) sample range to a probed duration (minus a small
    margin, since ffmpeg can refuse to seek to the very last frame) and to
    t0 >= 0. Raises ValueError when the clamped range is empty."""
    t0 = max(0.0, t0)
    if dur > 0:
        t1 = min(t1, max(0.0, dur - margin))
    if t1 < t0:
        raise ValueError(
            f"clamped sample range is empty ({clock(t0)}..{clock(t1)}) -- "
            "the requested range starts within the final "
            f"{margin:g}s of the video, or past its end"
        )
    return t0, t1


def sample_times(t0, t1, fps):
    """Every sample instant from t0 to t1 inclusive, 1/fps seconds apart."""
    if fps <= 0:
        raise ValueError("--fps must be greater than 0")
    step = 1.0 / fps
    times, t = [], t0
    while t <= t1 + 1e-6:
        times.append(t)
        t += step
    return times


def plan_samples(times, merge, settle, dur):
    """Group scene-change instants into samples.

    Change times closer together than ``merge`` seconds (an animation's
    steps) collapse into one group; a group's span is additionally capped at
    3x ``merge`` so a long, slow drift of small changes doesn't merge forever.
    Each group is sampled ``settle`` seconds after its last change, capped to
    just before the probed duration. An opening frame is added when nothing
    already samples the first ``settle`` seconds.

    Returns a list of dicts: ``first`` (the group's first change time),
    ``last`` (its last), ``n`` (how many raw changes merged into it) and
    ``sample`` (the clamped instant to actually grab).
    """
    groups = []
    for t in times:
        if groups and t - groups[-1]["last"] < merge and t - groups[-1]["first"] < 3 * merge:
            groups[-1]["last"] = t
            groups[-1]["n"] += 1
        else:
            groups.append({"first": t, "last": t, "n": 1})
    cap = dur - 0.5 if dur > 0 else float("inf")
    if not groups or groups[0]["first"] > settle:
        groups.insert(0, {"first": 0.0, "last": 0.0, "n": 1})
    for g in groups:
        g["sample"] = min(g["last"] + settle, cap)
    return groups


def sparse_gaps(sample_seconds, threshold):
    """(from, to) pairs for every gap between consecutive sample instants
    longer than ``threshold`` -- a stretch that may be hiding a slide the
    scene-change threshold missed."""
    return [
        (sample_seconds[i], sample_seconds[i + 1])
        for i in range(len(sample_seconds) - 1)
        if sample_seconds[i + 1] - sample_seconds[i] > threshold
    ]


def dhash(gray, w=None, h=None):
    """A difference hash from packed 8-bit grayscale pixels (w*h bytes,
    row-major, w = grab width = hash width + 1). Comparing each pixel only to
    its right neighbour, never to an absolute brightness, is what makes it
    survive uniform dimming (a faded animation step of a slide already kept)."""
    w = HASH_W + 1 if w is None else w
    h = HASH_H if h is None else h
    if len(gray) != w * h:
        raise ValueError(f"expected {w * h} grayscale bytes, got {len(gray)}")
    bits = 0
    for row in range(h):
        base = row * w
        for col in range(w - 1):
            bits = (bits << 1) | (1 if gray[base + col] > gray[base + col + 1] else 0)
    return bits


def hamming(a, b):
    return bin(a ^ b).count("1")


def dedupe(hashes, threshold):
    """For every hash, in order, the index of the earliest previous
    non-duplicate within ``threshold`` Hamming distance, or None. A sample is
    only ever compared against kept (non-duplicate) samples, so a run of near-
    identical dimmed steps all point back at the same first appearance."""
    kept, dup_of = [], []
    for i, h in enumerate(hashes):
        match = next((j for j in kept if hamming(h, hashes[j]) <= threshold), None)
        dup_of.append(match)
        if match is None:
            kept.append(i)
    return dup_of


def tile_regions(width=None):
    """The (name, ffmpeg filter) pairs the `tile` reading helper renders for
    one slide: the whole slide, its top and bottom halves, and a footer band
    (the bottom ~15%, where full-screen layouts print faint source lines) --
    each scaled/cropped-then-upscaled to a consistent reading width."""
    w = TILE_READ_W if width is None else width
    return [
        ("full", f"scale={w}:-2"),
        ("top", f"crop=iw:ih*0.5:0:0,scale={w}:-2"),
        ("bottom", f"crop=iw:ih*0.5:0:ih*0.5,scale={w}:-2"),
        ("footer", f"crop=iw:ih*0.15:0:ih*0.85,scale={w}:-2"),
    ]


def _first_frame(cmd_for_time, t, validate, run_fn=None):
    """Try grabbing a frame at ``t``, then one second earlier, returning the
    time that worked. ffmpeg exits non-zero when ``-ss`` lands past the last
    frame, so both attempts must survive a failed run() rather than raise."""
    run_fn = run_fn or run  # resolved at call time, so tests can patch `run`
    last = None
    for at in (t, max(t - 1.0, 0.0)):
        last = run_fn(cmd_for_time(at))
        if last.returncode == 0 and validate(last):
            return at, last
    return None, last


def grab(vid_path, t, out, label, crop=None, extra_vf=None):
    vf = (f"crop={crop}," if crop else "") + f"scale={TILE_W}:-2" + (f",{extra_vf}" if extra_vf else "") + label_filter(label)

    def cmd(at):
        return [tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{at:.2f}", "-i", vid_path,
                "-frames:v", "1", "-vf", vf, "-q:v", "3", out]

    at, _ = _first_frame(cmd, t, lambda p: os.path.exists(out) and os.path.getsize(out) > 0)
    if at is None:
        die(f"no frame at {t:.2f}s")


def thumbnail_hash(vid_path, t, crop=None):
    """The dHash of a tiny grayscale thumbnail at ``t``, for de-duplication."""
    w, h = HASH_W + 1, HASH_H
    vf = (f"crop={crop}," if crop else "") + f"scale={w}:{h},format=gray"

    def cmd(at):
        return [tool("ffmpeg"), "-v", "error", "-ss", f"{at:.2f}", "-i", vid_path,
                "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-"]

    at, p = _first_frame(cmd, t, lambda p: len(p.stdout or b"") == w * h, run_fn=lambda c: run(c, text=False))
    if at is None:
        die(f"no frame at {t:.2f}s for de-duplication")
    return dhash(p.stdout, w, h)


def sheets(frames_dir, pattern, out_prefix, cols):
    for old in glob.glob(out_prefix + "-*.jpg"):
        os.remove(old)
    if not glob.glob(os.path.join(frames_dir, pattern.replace("%03d", "*"))):
        return []
    p = run([tool("ffmpeg"), "-v", "error", "-y", "-framerate", "1", "-i", os.path.join(frames_dir, pattern),
             "-vf", f"tile={cols}x{cols}:padding=4", "-q:v", "4", out_prefix + "-%d.jpg"])
    if p.returncode != 0:
        die(f"could not build a contact sheet from {frames_dir}: {(p.stderr or '')[-300:]}")
    return sorted(glob.glob(out_prefix + "-*.jpg"), key=lambda path: int(re.search(r"-(\d+)\.jpg$", path).group(1)))


def cmd_fetch(a):
    need("yt-dlp")
    vid = transcript(a.dir)
    work = workdir(vid)
    fmt = f"bv[height<={a.height}][ext=mp4]/bv[height<={a.height}]"
    result = yt.run_ytdlp(["-q", "--no-warnings", "-f", fmt,
                           "-o", os.path.join(work, "video.%(ext)s"), f"https://www.youtube.com/watch?v={vid}"])
    if result.returncode != 0:
        raise yt.ytdlp_failure(result)
    print(f"VIDEO={video(work)}")
    print(f"WORK={work}")


def cmd_detect(a):
    need("ffmpeg")
    need("ffprobe")
    vid = transcript(a.dir)
    work = workdir(vid)
    v = video(work)
    crop = crop_expr(a.crop)
    vf = f"fps={a.fps}" + (f",crop={crop}" if crop else "") + f",select='gt(scene,{a.threshold})',showinfo"
    p = run([tool("ffmpeg"), "-hide_banner", "-i", v, "-vf", vf, "-an", "-f", "null", "-"])
    if p.returncode:
        cause = [line for line in (p.stderr or "").splitlines() if re.search(r"Error|Invalid", line)]
        die(f"ffmpeg scene detection failed: {' | '.join(cause[-3:]) or (p.stderr or '')[-300:]}")
    times = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", p.stderr or "")]

    dur = duration(v)
    groups = plan_samples(times, a.merge, a.settle, dur)
    hashes = [thumbnail_hash(v, g["sample"], crop) for g in groups]
    dup_of = dedupe(hashes, a.dup_threshold)

    with io.open(os.path.join(work, "samples.tsv"), "w", encoding="utf-8") as f:
        f.write("index\tfirst\tsample_seconds\tsample_clock\tdup_of\n")
        for i, g in enumerate(groups):
            f.write(f"{i}\t{clock(g['first'])}\t{g['sample']:.2f}\t{clock(g['sample'])}\t"
                    f"{dup_of[i] if dup_of[i] is not None else ''}\n")

    print(f"CHANGES={len(times)} SAMPLES={len(groups)} (merged within {a.merge}s, sampled {a.settle}s after; "
          "cells are labelled 'index time')")
    for i, g in enumerate(groups):
        print(f"  {i:3d} first={clock(g['first'])} t={g['sample']:.2f}")
        if dup_of[i] is not None:
            print(f"DUP {i} of={dup_of[i]}")
        if g["n"] >= 2:
            print(f"BURST {i} first={clock(g['first'])} last={clock(g['last'])} n={g['n']} changes -- "
                  f"resample: slides.py sample --dir {shlex.quote(a.dir)} --from {g['first']:.2f} "
                  f"--to {g['last'] + a.settle:.2f} --fps 4 --crop {shlex.quote(a.crop)}")
    sample_seconds = [g["sample"] for g in groups]
    if dur > 0:
        sample_seconds = sample_seconds + [dur - 0.5]  # the tail stretch to the video's end is a gap too
    for from_t, to_t in sparse_gaps(sample_seconds, a.sparse):
        print(f"SPARSE {clock(from_t)}..{clock(to_t)}: one sample in {to_t - from_t:.0f}s -- "
              f"try a lower --threshold, or `sample --from {clock(from_t)} --to {clock(to_t)}`")

    fdir = os.path.join(work, "frames")
    shutil.rmtree(fdir, ignore_errors=True)
    os.makedirs(fdir)
    kept = [i for i in range(len(groups)) if a.keep_dups or dup_of[i] is None]
    for seq, i in enumerate(kept):
        grab(v, groups[i]["sample"], os.path.join(fdir, f"f{seq:03d}.jpg"), f"{i} {clock(groups[i]['sample'])}")
    for sh in sheets(fdir, "f%03d.jpg", os.path.join(work, "sheet"), 4):
        print(f"SHEET={sh}")


def cmd_sample(a):
    need("ffmpeg")
    need("ffprobe")
    vid = transcript(a.dir)
    work = workdir(vid)
    v = video(work)
    dur = duration(v)
    t0, t1 = clamp_range(secs(a.from_), secs(a.to), dur)
    crop = crop_expr(a.crop)
    times = sample_times(t0, t1, a.fps)
    d = os.path.join(work, f"fine-{stamp(t0)}-{stamp(t1)}")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    for k, t in enumerate(times):
        grab(v, t, os.path.join(d, f"g{k:03d}.jpg"), f"{t:.1f}s", crop)
    print(f"FRAMES={len(times)} every {1.0 / a.fps:g}s from {clock(t0)}; each cell is labelled with its time in seconds")
    for sh in sheets(d, "g%03d.jpg", os.path.join(d, "sheet"), 6):
        print(f"SHEET={sh}")


def cmd_layout(a):
    need("ffmpeg")
    need("ffprobe")
    vid = transcript(a.dir)
    work = workdir(vid)
    v = video(work)
    if a.at:
        times = [secs(x) for x in a.at.split(",")]
    else:
        dur = duration(v)
        if not dur:
            die("could not probe the video's duration for --at percentages; pass --at with explicit times")
        times = [dur * p for p in (0.10, 0.35, 0.60, 0.85)]

    ldir = os.path.join(work, "layout")
    shutil.rmtree(ldir, ignore_errors=True)
    os.makedirs(ldir)
    grid = "drawgrid=w=iw*0.1:h=ih*0.1:t=1:c=white@0.5,drawgrid=w=iw*0.5:h=ih*0.5:t=3:c=red@0.8"
    for i, t in enumerate(times):
        grab(v, t, os.path.join(ldir, f"l{i:03d}.jpg"), clock(t), extra_vf=grid)
    for sh in sheets(ldir, "l%03d.jpg", os.path.join(ldir, "sheet"), 2):
        print(f"SHEET={sh}")
    print("Read the 10% grid (heavier line at 50%) and express the slide region as "
          "W,H,X,Y fractions for --crop, e.g. iw*0.7,ih*0.62,iw*0.29,0")


def cmd_extract(a):
    need("ffmpeg")
    vid = transcript(a.dir)
    work = workdir(vid)
    v = video(work)
    crop = crop_expr(a.crop)
    out = os.path.join(a.dir, "slides")
    os.makedirs(out, exist_ok=True)
    for pick in a.pick:
        ts, _, name = pick.partition("=")
        name = safe_name(name)
        t = secs(ts)
        dst = os.path.join(out, f"{stamp(t)}-{name}.webp")
        if os.path.exists(dst):
            print(f"SKIPPED {dst} (exists; slides are raw capture and never overwritten)")
            continue
        tmp = os.path.join(work, "extract.webp")
        if os.path.exists(tmp):
            os.remove(tmp)
        cmd = [tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", v, "-frames:v", "1"]
        if crop:
            cmd += ["-vf", f"crop={crop}"]
        cmd += ["-c:v", "libwebp", "-quality", str(a.quality), tmp]
        p = run(cmd)
        if p.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
            die(f"--pick {pick}: no frame at {t:.2f}s (past the end?)")
        with open(tmp, "rb") as s, open(dst, "xb") as d:
            shutil.copyfileobj(s, d)
        print(f"OK {dst} ({os.path.getsize(dst) // 1024} KB)")


def cmd_tile(a):
    need("ffmpeg")
    vid = transcript(a.dir)
    work = workdir(vid)
    if a.slide:
        paths = [os.path.abspath(p) for p in a.slide]
    else:
        paths = sorted(
            p for p in glob.glob(os.path.join(a.dir, "slides", "*"))
            if os.path.splitext(p)[1].lower() in (".webp", ".png", ".jpg", ".jpeg", ".gif")
        )
    if not paths:
        die(f"no slide images found under {a.dir}/slides -- pass --slide")

    tdir = os.path.join(work, "tiles")
    os.makedirs(tdir, exist_ok=True)
    for path in paths:
        base = os.path.splitext(os.path.basename(path))[0]
        for name, crop in tile_regions():
            out = os.path.join(tdir, f"{base}-{name}.jpg")
            p = run([tool("ffmpeg"), "-v", "error", "-y", "-i", path, "-frames:v", "1", "-vf", crop,
                     "-q:v", "3", out])
            if p.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
                die(f"could not render the '{name}' reading crop for {path}: {(p.stderr or '')[-300:]}")
            print(f"TILE={out}")


def cmd_add_shot(a):
    transcript(a.dir)
    ext = os.path.splitext(a.file)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        die(f"{a.file} is not an image (.png .jpg .jpeg .webp .gif)")
    out = os.path.join(a.dir, "slides")
    os.makedirs(out, exist_ok=True)
    dst = os.path.join(out, f"{stamp(secs(a.at))}-{safe_name(a.name)}{ext}")
    try:
        with open(a.file, "rb") as s, open(dst, "xb") as d:
            shutil.copyfileobj(s, d)
    except FileExistsError:
        die(f"{dst} exists; slides are never overwritten")
    print(f"OK {dst} (copied byte for byte)")


def main():
    yt.utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("fetch")
    s.add_argument("--dir", required=True)
    s.add_argument("--height", type=int, default=1080)
    s.set_defaults(fn=cmd_fetch)

    s = sub.add_parser("detect")
    s.add_argument("--dir", required=True)
    s.add_argument("--crop", default="full")
    s.add_argument("--threshold", type=float, default=0.04,
                    help="scene-change sensitivity; lower catches lighter/text-only changes. "
                         "0.04 default; try 0.12 for a busy overlay layout")
    s.add_argument("--merge", type=float, default=2.5)
    s.add_argument("--settle", type=float, default=1.5)
    s.add_argument("--fps", type=float, default=2)
    s.add_argument("--sparse", type=float, default=180,
                    help="warn when consecutive samples are more than this many seconds apart")
    s.add_argument("--dup-threshold", type=int, default=DEFAULT_DUP_THRESHOLD, dest="dup_threshold",
                    help="max Hamming distance between two samples' hashes to call them duplicates")
    s.add_argument("--keep-dups", action="store_true",
                    help="include duplicate samples in the contact sheets instead of leaving them out")
    s.set_defaults(fn=cmd_detect)

    s = sub.add_parser("sample")
    s.add_argument("--dir", required=True)
    s.add_argument("--from", dest="from_", required=True)
    s.add_argument("--to", required=True)
    s.add_argument("--fps", type=float, default=1)
    s.add_argument("--crop", default="full")
    s.set_defaults(fn=cmd_sample)

    s = sub.add_parser("layout")
    s.add_argument("--dir", required=True)
    s.add_argument("--at", default=None,
                    help="comma-separated times; default 10%%/35%%/60%%/85%% of the probed duration")
    s.set_defaults(fn=cmd_layout)

    s = sub.add_parser("extract")
    s.add_argument("--dir", required=True)
    s.add_argument("--pick", action="append", required=True)
    s.add_argument("--quality", type=int, default=82)
    s.add_argument("--crop", default="full",
                    help="full (default), a named preset, or W,H,X,Y -- a crop can cut slide "
                         "content, so read the extracted slide back")
    s.set_defaults(fn=cmd_extract)

    s = sub.add_parser("tile")
    s.add_argument("--dir", required=True)
    s.add_argument("--slide", action="append", default=None,
                    help="a slide image to tile; repeatable. Default: every image in <dir>/slides/")
    s.set_defaults(fn=cmd_tile)

    s = sub.add_parser("add-shot")
    s.add_argument("--dir", required=True)
    s.add_argument("--file", required=True)
    s.add_argument("--at", required=True)
    s.add_argument("--name", required=True)
    s.set_defaults(fn=cmd_add_shot)

    a = ap.parse_args()
    a.dir = os.path.abspath(a.dir)
    try:
        a.fn(a)
    except Exception as e:
        die(str(e))


if __name__ == "__main__":
    main()
