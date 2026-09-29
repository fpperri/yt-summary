import argparse
import os
import shutil as _shutil
import subprocess
from types import SimpleNamespace

import pytest

import slides
from slides import (
    CROPS,
    clamp_range,
    clock,
    cmd_add_shot,
    cmd_detect,
    cmd_extract,
    cmd_fetch,
    cmd_layout,
    cmd_sample,
    cmd_tile,
    crop_expr,
    dedupe,
    dhash,
    find_font,
    hamming,
    plan_samples,
    safe_name,
    sample_times,
    secs,
    sparse_gaps,
    stamp,
    tile_regions,
    transcript,
    workdir,
)

VID = "dQw4w9WgXcQ"


def write_source(tmp_path, vid=VID, slug="chan-title"):
    folder = tmp_path / "source" / slug
    folder.mkdir(parents=True)
    (folder / f"transcript.{slug}.md").write_text(
        f'---\nvideo_id: "{vid}"\n---\n# t\n', encoding="utf-8"
    )
    return folder


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------

class TestSecsClockStamp:
    @pytest.mark.parametrize("text,expected", [
        ("5", 5.0), ("1:05", 65.0), ("1:01:05", 3665.0), ("12.5", 12.5),
    ])
    def test_secs_parses(self, text, expected):
        assert secs(text) == expected

    def test_secs_rejects_empty(self):
        with pytest.raises(ValueError):
            secs("")

    @pytest.mark.parametrize("t,expected", [(0, "00:00"), (65, "01:05"), (3665, "01:01:05")])
    def test_clock(self, t, expected):
        assert clock(t) == expected

    @pytest.mark.parametrize("t,expected", [(0, "0000"), (65, "0105"), (3665, "010105")])
    def test_stamp(self, t, expected):
        assert stamp(t) == expected


class TestCropExpr:
    def test_full_is_no_crop(self):
        assert crop_expr("full") is None

    def test_default_is_full(self):
        assert crop_expr(None) is None
        assert crop_expr("") is None

    def test_named_presets_resolve(self):
        assert crop_expr("aie") == CROPS["aie"]
        assert crop_expr("overlay") == CROPS["overlay"]

    def test_case_insensitive_preset(self):
        assert crop_expr("AIE") == CROPS["aie"]

    def test_custom_whx_expression(self):
        assert crop_expr("iw*0.7,ih*0.6,iw*0.29,0") == "iw*0.7:ih*0.6:iw*0.29:0"

    def test_rejects_wrong_arity(self):
        with pytest.raises(SystemExit):
            crop_expr("1,2,3")


class TestSafeName:
    def test_lowercases_and_dashes(self):
        assert safe_name("Agent Memory!") == "agent-memory"

    def test_collapses_repeated_dashes(self):
        assert safe_name("a---b") == "a-b"

    def test_strips_leading_trailing_dashes(self):
        assert safe_name("--x--") == "x"

    def test_dies_when_nothing_usable(self):
        with pytest.raises(SystemExit):
            safe_name("!!!")


class TestPlanSamples:
    def test_isolated_changes_become_their_own_groups(self):
        groups = plan_samples([10.0, 30.0], merge=2.5, settle=1.5, dur=0)
        # An opening frame is inserted (10.0 > settle), so three groups.
        assert [g["first"] for g in groups] == [0.0, 10.0, 30.0]
        assert [g["sample"] for g in groups] == [1.5, 11.5, 31.5]
        assert [g["n"] for g in groups] == [1, 1, 1]

    def test_close_changes_merge_into_one_group(self):
        groups = plan_samples([10.0, 11.0, 11.8], merge=2.5, settle=1.5, dur=0)
        merged = [g for g in groups if g["first"] == 10.0][0]
        assert merged["last"] == 11.8
        assert merged["n"] == 3
        assert merged["sample"] == pytest.approx(13.3)

    def test_merge_cap_stops_a_group_growing_forever(self):
        """A group's span is capped at 3x merge even if each successive gap
        is under the merge window -- otherwise a slow drift never closes."""
        # merge=1.0: gaps of 0.9 keep chaining, but the span cap (3.0) must
        # cut the group before it swallows every following change too.
        times = [0.0, 0.9, 1.8, 2.7, 3.6, 4.5]
        groups = plan_samples(times, merge=1.0, settle=0.0, dur=0)
        first_group = groups[0]
        assert first_group["first"] == 0.0
        assert first_group["last"] < 3.6  # span capped below the full run

    def test_no_opening_frame_when_first_change_is_within_settle(self):
        groups = plan_samples([1.0, 30.0], merge=2.5, settle=1.5, dur=0)
        assert [g["first"] for g in groups] == [1.0, 30.0]

    def test_empty_changes_still_yields_an_opening_frame(self):
        groups = plan_samples([], merge=2.5, settle=1.5, dur=0)
        assert len(groups) == 1
        assert groups[0]["first"] == 0.0
        assert groups[0]["sample"] == 1.5

    def test_sample_is_capped_just_before_the_probed_duration(self):
        groups = plan_samples([95.0], merge=2.5, settle=5.0, dur=100.0)
        last = groups[-1]
        assert last["sample"] == 99.5  # dur - 0.5, not 100.0

    def test_no_cap_when_duration_unknown(self):
        groups = plan_samples([10.0], merge=2.5, settle=5.0, dur=0)
        assert groups[-1]["sample"] == 15.0


class TestClampRange:
    def test_leaves_a_range_inside_the_video_untouched(self):
        assert clamp_range(5.0, 20.0, dur=100.0) == (5.0, 20.0)

    def test_clamps_to_to_the_probed_duration_minus_margin(self):
        assert clamp_range(5.0, 999.0, dur=100.0) == (5.0, 99.5)

    def test_clamps_from_up_to_zero(self):
        assert clamp_range(-10.0, 20.0, dur=100.0) == (0.0, 20.0)

    def test_unknown_duration_leaves_to_untouched(self):
        assert clamp_range(5.0, 999.0, dur=0) == (5.0, 999.0)

    def test_raises_when_the_clamped_range_is_empty(self):
        with pytest.raises(ValueError):
            clamp_range(200.0, 300.0, dur=100.0)

    def test_error_distinguishes_the_final_margin_from_truly_past_the_end(self):
        """A --from that lands inside the video but within its last 0.5s
        margin is not "entirely past the end" -- that phrase belongs to a
        --from genuinely beyond the probed duration."""
        with pytest.raises(ValueError, match="final"):
            clamp_range(35.6, 40.0, dur=35.9)


class TestSampleTimes:
    def test_generates_inclusive_of_the_end(self):
        assert sample_times(0.0, 2.0, fps=1) == [0.0, 1.0, 2.0]

    def test_fps_controls_the_step(self):
        assert sample_times(0.0, 1.0, fps=2) == [0.0, 0.5, 1.0]

    def test_rejects_non_positive_fps(self):
        with pytest.raises(ValueError):
            sample_times(0.0, 1.0, fps=0)

    def test_product_demo_long_range_low_fps_is_correct_and_clamped(self):
        """The product-demo mode: --fps 0.25 over a long, already-clamped range."""
        t0, t1 = clamp_range(0.0, 10_000.0, dur=3600.0)
        assert (t0, t1) == (0.0, 3599.5)
        times = sample_times(t0, t1, fps=0.25)
        assert times[0] == 0.0
        assert times[-1] <= 3599.5
        assert len(times) == len(set(times))  # strictly increasing, no dupes
        # 4-second steps across ~3599.5s
        assert 899 <= len(times) <= 901


class TestSparseGaps:
    def test_flags_a_long_gap(self):
        assert sparse_gaps([0.0, 10.0, 400.0], threshold=180) == [(10.0, 400.0)]

    def test_no_gaps_under_threshold(self):
        assert sparse_gaps([0.0, 10.0, 20.0], threshold=180) == []

    def test_single_sample_has_no_gaps(self):
        assert sparse_gaps([5.0], threshold=180) == []


class TestDhashAndHamming:
    def test_identical_bytes_have_zero_distance(self):
        data = bytes((i * 7) % 256 for i in range(17 * 16))
        assert hamming(dhash(data, 17, 16), dhash(data, 17, 16)) == 0

    def test_ascending_and_descending_gradients_are_far_apart(self):
        w, h = 17, 16
        asc = bytes([col * 15 for col in range(w)] * h)
        desc = bytes([255 - col * 15 for col in range(w)] * h)
        assert hamming(dhash(asc, w, h), dhash(desc, w, h)) >= (w - 1) * h - 4

    def test_uniform_image_hashes_to_all_zero_bits(self):
        w, h = 17, 16
        flat = bytes([128] * (w * h))
        assert dhash(flat, w, h) == 0

    def test_wrong_byte_count_raises(self):
        with pytest.raises(ValueError):
            dhash(b"\x00" * 5, 17, 16)


class TestDedupe:
    def test_no_duplicates_when_all_far_apart(self):
        hashes = [0b0000, 0b1111, 0b0011]
        assert dedupe(hashes, threshold=0) == [None, None, None]

    def test_later_sample_within_threshold_points_at_the_earliest_match(self):
        hashes = [0b0000, 0b1111, 0b0001, 0b1110]
        # index 2 is 1 bit off index 0; index 3 is 1 bit off index 1.
        assert dedupe(hashes, threshold=1) == [None, None, 0, 1]

    def test_a_run_of_near_duplicates_chains_through_kept_samples(self):
        """Each sample is compared only against kept (non-duplicate) samples,
        so a chain where each step is 1 bit from the last, but 2+ from the
        first, still resolves: index 1 is a dup of 0, index 2 is too far from
        the only kept sample (0) to match, index 3 matches the newly-kept 2."""
        hashes = [0b0000, 0b0001, 0b0011, 0b0111]
        assert dedupe(hashes, threshold=1) == [None, 0, None, 2]

    def test_empty_input(self):
        assert dedupe([], threshold=5) == []


class TestTileRegions:
    def test_returns_full_top_bottom_footer(self):
        names = [name for name, _ in tile_regions()]
        assert names == ["full", "top", "bottom", "footer"]

    def test_footer_crops_the_bottom_band(self):
        crop = dict(tile_regions())["footer"]
        assert "ih*0.15" in crop and "ih*0.85" in crop

    def test_width_is_configurable(self):
        crop = dict(tile_regions(width=800))["full"]
        assert "800" in crop


class TestFindFont:
    def test_returns_first_existing_candidate(self, monkeypatch):
        monkeypatch.setattr(slides, "FONT_CANDIDATES", ["/nope/a.ttf", "/nope/b.ttf", "/yes/c.ttf"])
        monkeypatch.setattr(os.path, "exists", lambda p: p == "/yes/c.ttf")
        assert find_font() == "/yes/c.ttf"

    def test_none_when_nothing_exists(self, monkeypatch):
        monkeypatch.setattr(slides, "FONT_CANDIDATES", ["/nope/a.ttf"])
        monkeypatch.setattr(os.path, "exists", lambda p: False)
        assert find_font() is None


class TestLabelFilter:
    def test_empty_without_a_font(self, monkeypatch):
        monkeypatch.setattr(slides, "find_font", lambda: None)
        assert slides.label_filter("0 00:05") == ""

    def test_includes_drawtext_with_a_font(self, monkeypatch):
        monkeypatch.setattr(slides, "find_font", lambda: "/some/font.ttf")
        out = slides.label_filter("0 00:05")
        assert "drawtext" in out
        assert "00\\:05" in out


# ---------------------------------------------------------------------------
# transcript() / workdir()
# ---------------------------------------------------------------------------

class TestTranscript:
    def test_reads_id_via_ingest_video(self, tmp_path):
        folder = write_source(tmp_path)
        assert transcript(folder) == VID

    def test_dies_without_a_transcript_file(self, tmp_path):
        (tmp_path / "empty").mkdir()
        with pytest.raises(SystemExit):
            transcript(tmp_path / "empty")

    def test_dies_on_unreadable_video_id(self, tmp_path):
        folder = tmp_path / "bad"
        folder.mkdir()
        (folder / "transcript.bad.md").write_text("---\nvideo_id: nope\n---\n", encoding="utf-8")
        with pytest.raises(SystemExit):
            transcript(folder)


class TestWorkdir:
    def test_lands_under_the_overridden_root(self, tmp_path, monkeypatch):
        root = tmp_path / "workroot"
        monkeypatch.setenv(slides.WORKROOT_ENV, str(root))
        w = workdir(VID)
        assert w == str(root.resolve() / "ingest-slides" / VID)
        assert os.path.isdir(w)

    def test_refuses_a_root_inside_a_git_repository(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        nested = repo / "tmp"
        nested.mkdir()
        monkeypatch.setenv(slides.WORKROOT_ENV, str(nested))
        with pytest.raises(SystemExit):
            workdir(VID)


# ---------------------------------------------------------------------------
# Command-level tests -- the subprocess boundary (`slides.run`, `yt.run_ytdlp`,
# `shutil.which`) is faked; no real ffmpeg/yt-dlp is invoked here.
# ---------------------------------------------------------------------------

def _fake_tools(monkeypatch):
    monkeypatch.setattr(slides.shutil, "which", lambda name: f"/fake/{name}")


def _mkargs(**kw):
    return argparse.Namespace(**kw)


class TestCmdFetch:
    def test_writes_the_video_and_prints_paths(self, tmp_path, monkeypatch, capsys):
        folder = write_source(tmp_path)
        monkeypatch.setenv(slides.WORKROOT_ENV, str(tmp_path / "workroot"))

        def fake_run_ytdlp(args, timeout=None):
            out_pattern = args[args.index("-o") + 1]
            path = out_pattern.replace("%(ext)s", "mp4")
            with open(path, "wb") as fh:
                fh.write(b"fake-video")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(slides.yt, "run_ytdlp", fake_run_ytdlp)
        cmd_fetch(_mkargs(dir=str(folder), height=360))
        out = capsys.readouterr().out
        assert "VIDEO=" in out and "video.mp4" in out
        assert "WORK=" in out

    def test_a_classified_failure_is_raised(self, tmp_path, monkeypatch):
        folder = write_source(tmp_path)
        monkeypatch.setenv(slides.WORKROOT_ENV, str(tmp_path / "workroot"))
        monkeypatch.setattr(
            slides.yt, "run_ytdlp",
            lambda args, timeout=None: SimpleNamespace(returncode=1, stdout="", stderr="ERROR: Video unavailable"),
        )
        with pytest.raises(RuntimeError):
            cmd_fetch(_mkargs(dir=str(folder), height=360))

    def test_dies_clearly_when_yt_dlp_is_missing(self, tmp_path, monkeypatch):
        """A missing yt-dlp must fail with this script's own clear message,
        not surface as whatever raw OS error subprocess raises for a program
        that isn't on PATH."""
        folder = write_source(tmp_path)
        monkeypatch.setenv(slides.WORKROOT_ENV, str(tmp_path / "workroot"))
        monkeypatch.setattr(slides.shutil, "which", lambda name: None)
        with pytest.raises(SystemExit, match="yt-dlp not found on PATH"):
            cmd_fetch(_mkargs(dir=str(folder), height=360))


class DetectFakeRun:
    """Fakes the ffmpeg/ffprobe subprocess boundary for cmd_detect: scene
    detection, duration probing, per-sample thumbnail hashing, per-sample
    frame grabs and contact-sheet tiling."""

    def __init__(self, change_times, dur, hash_by_ss):
        self.change_times = change_times
        self.dur = dur
        self.hash_by_ss = hash_by_ss

    def __call__(self, cmd, text=True):
        joined = " ".join(cmd)
        if "csv=p=0" in joined:
            return SimpleNamespace(returncode=0, stdout=f"{self.dur}\n", stderr="")
        if "-f" in cmd and "null" in cmd:
            stderr = " ".join(f"pts_time:{t}" for t in self.change_times)
            return SimpleNamespace(returncode=0, stdout="", stderr=stderr)
        if "rawvideo" in cmd:
            ss = cmd[cmd.index("-ss") + 1]
            data = self.hash_by_ss[ss]
            return SimpleNamespace(returncode=0, stdout=data, stderr=b"")
        if "-frames:v" in cmd:
            out = cmd[-1]
            with open(out, "wb") as fh:
                fh.write(b"fake-jpg")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "tile=" in joined:
            out_pattern = cmd[-1]
            with open(out_pattern.replace("%d", "1"), "wb") as fh:
                fh.write(b"fake-sheet")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command in test: {cmd}")


_HASH_GRAB_W, _HASH_GRAB_H = slides.HASH_W + 1, slides.HASH_H
HASH_A = bytes([int(col * 255 / (_HASH_GRAB_W - 1)) for col in range(_HASH_GRAB_W)] * _HASH_GRAB_H)
HASH_B = bytes([255 - b for b in HASH_A])


class TestCmdDetect:
    def _args(self, folder, **overrides):
        base = dict(dir=str(folder), crop="full", threshold=0.04, merge=2.5, settle=1.5,
                    fps=2, sparse=1, dup_threshold=14, keep_dups=False)
        base.update(overrides)
        return _mkargs(**base)

    def _setup(self, tmp_path, monkeypatch):
        _fake_tools(monkeypatch)
        folder = write_source(tmp_path)
        monkeypatch.setenv(slides.WORKROOT_ENV, str(tmp_path / "workroot"))
        work = workdir(VID)
        with open(os.path.join(work, "video.mp4"), "wb") as fh:
            fh.write(b"fake")
        return folder

    def test_marks_a_repeated_sample_as_dup_of_the_earlier_one(self, tmp_path, monkeypatch, capsys):
        folder = self._setup(tmp_path, monkeypatch)
        # times -> groups: opening(sample 1.5, HASH_A), burst 5.0/5.3/5.6
        # (sample 7.1, HASH_B), isolated 50.0 (sample 51.5, HASH_B again -> dup).
        fake = DetectFakeRun(
            change_times=[5.0, 5.3, 5.6, 50.0], dur=60.0,
            hash_by_ss={"1.50": HASH_A, "7.10": HASH_B, "51.50": HASH_B},
        )
        monkeypatch.setattr(slides, "run", fake)
        cmd_detect(self._args(folder))
        out = capsys.readouterr().out
        assert "CHANGES=4 SAMPLES=3" in out
        assert "DUP 2 of=1" in out
        assert "BURST 1 first=00:05 last=00:05 n=3 changes" in out
        assert "SPARSE" in out  # --sparse 1 makes every real gap here sparse

    def test_a_two_change_flash_group_still_gets_a_burst_cue(self, tmp_path, monkeypatch, capsys):
        """A one-second flash (a scene change in, then back out) merges into
        a single group with n=2, not the n>=3 an animation build-up usually
        produces. It still deserves a BURST cue -- without one, the flash's
        own content is never sampled or flagged, only silently merged away."""
        folder = self._setup(tmp_path, monkeypatch)
        fake = DetectFakeRun(
            change_times=[5.0, 5.3], dur=10.0,
            hash_by_ss={"1.50": HASH_A, "6.80": HASH_B},
        )
        monkeypatch.setattr(slides, "run", fake)
        cmd_detect(self._args(folder))
        out = capsys.readouterr().out
        assert "BURST 1 first=00:05 last=00:05 n=2 changes" in out

    def test_burst_resample_command_quotes_a_dir_with_spaces(self, tmp_path, monkeypatch, capsys):
        """A source folder with spaces in its path must survive a copy-paste
        of the suggested resample command as one argument, not split apart."""
        folder = self._setup(tmp_path, monkeypatch)
        spaced = folder.parent / "chan title"
        os.rename(folder, spaced)
        fake = DetectFakeRun(
            change_times=[5.0, 5.3], dur=10.0,
            hash_by_ss={"1.50": HASH_A, "6.80": HASH_B},
        )
        monkeypatch.setattr(slides, "run", fake)
        cmd_detect(self._args(spaced))
        out = capsys.readouterr().out
        assert f"--dir {slides.shlex.quote(str(spaced))}" in out

    def test_burst_resample_command_uses_raw_seconds_not_truncated_clock(self, tmp_path, monkeypatch, capsys):
        """The printed resample command must cover the whole burst span in
        fractional seconds -- clock() truncates to whole seconds, which can
        clip a sub-second slide (a scene change at 5.3s and another at 5.9s
        both round down to the same "00:05") right out of the suggested
        --from/--to range."""
        folder = self._setup(tmp_path, monkeypatch)
        fake = DetectFakeRun(
            change_times=[4.9, 5.3, 5.9], dur=10.0,
            hash_by_ss={"1.50": HASH_A, "7.40": HASH_B},
        )
        monkeypatch.setattr(slides, "run", fake)
        cmd_detect(self._args(folder))
        out = capsys.readouterr().out
        assert "--from 4.90 --to 7.40 --fps 4" in out

    def test_sparse_checks_the_tail_gap_to_the_probed_duration(self, tmp_path, monkeypatch, capsys):
        """A single early change on a long video must still get a SPARSE
        warning for the long silent stretch after it -- sparse_gaps only
        ever saw gaps between samples, never the gap from the last sample to
        the end of the probed video, which is exactly the case (a light
        slide slipping under the threshold) the warning exists for."""
        folder = self._setup(tmp_path, monkeypatch)
        fake = DetectFakeRun(change_times=[5.0], dur=1800.0, hash_by_ss={"1.50": HASH_A, "6.50": HASH_B})
        monkeypatch.setattr(slides, "run", fake)
        cmd_detect(self._args(folder, sparse=180))
        out = capsys.readouterr().out
        assert "SPARSE 00:06..29:59" in out

    def test_samples_tsv_carries_the_dup_of_column(self, tmp_path, monkeypatch):
        folder = self._setup(tmp_path, monkeypatch)
        fake = DetectFakeRun(
            change_times=[5.0, 5.3, 5.6, 50.0], dur=60.0,
            hash_by_ss={"1.50": HASH_A, "7.10": HASH_B, "51.50": HASH_B},
        )
        monkeypatch.setattr(slides, "run", fake)
        cmd_detect(self._args(folder))
        work = workdir(VID)
        rows = (open(os.path.join(work, "samples.tsv"), encoding="utf-8").read().splitlines())
        assert rows[0].split("\t") == ["index", "first", "sample_seconds", "sample_clock", "dup_of"]
        assert rows[3].split("\t")[-1] == "1"  # index 2's dup_of

    def test_dropped_by_default_but_kept_with_keep_dups(self, tmp_path, monkeypatch):
        folder = self._setup(tmp_path, monkeypatch)
        fake = DetectFakeRun(
            change_times=[5.0, 5.3, 5.6, 50.0], dur=60.0,
            hash_by_ss={"1.50": HASH_A, "7.10": HASH_B, "51.50": HASH_B},
        )
        monkeypatch.setattr(slides, "run", fake)
        cmd_detect(self._args(folder))
        work = workdir(VID)
        frames_default = len(os.listdir(os.path.join(work, "frames")))

        monkeypatch.setattr(slides, "run", fake)
        cmd_detect(self._args(folder, keep_dups=True))
        frames_keep = len(os.listdir(os.path.join(work, "frames")))
        assert frames_default == 2  # index 2 (the dup) left out
        assert frames_keep == 3


class TestCmdSample:
    def test_clamps_and_reports_frame_count(self, tmp_path, monkeypatch, capsys):
        _fake_tools(monkeypatch)
        folder = write_source(tmp_path)
        monkeypatch.setenv(slides.WORKROOT_ENV, str(tmp_path / "workroot"))
        work = workdir(VID)
        with open(os.path.join(work, "video.mp4"), "wb") as fh:
            fh.write(b"fake")

        fake = DetectFakeRun(change_times=[], dur=10.0, hash_by_ss={})
        monkeypatch.setattr(slides, "run", fake)
        cmd_sample(_mkargs(dir=str(folder), from_="0", to="100", fps=1, crop="full"))
        out = capsys.readouterr().out
        assert "FRAMES=10" in out  # 0..9.5 inclusive at 1fps -> 10 frames

    def test_dies_clearly_when_the_clamped_range_is_empty(self, tmp_path, monkeypatch):
        _fake_tools(monkeypatch)
        folder = write_source(tmp_path)
        monkeypatch.setenv(slides.WORKROOT_ENV, str(tmp_path / "workroot"))
        work = workdir(VID)
        with open(os.path.join(work, "video.mp4"), "wb") as fh:
            fh.write(b"fake")

        fake = DetectFakeRun(change_times=[], dur=10.0, hash_by_ss={})
        monkeypatch.setattr(slides, "run", fake)
        with pytest.raises(ValueError):
            cmd_sample(_mkargs(dir=str(folder), from_="200", to="300", fps=1, crop="full"))


class TestCmdExtract:
    def _setup(self, tmp_path, monkeypatch):
        _fake_tools(monkeypatch)
        folder = write_source(tmp_path)
        monkeypatch.setenv(slides.WORKROOT_ENV, str(tmp_path / "workroot"))
        work = workdir(VID)
        with open(os.path.join(work, "video.mp4"), "wb") as fh:
            fh.write(b"fake")
        return folder

    def _fake_ffmpeg_writes(self, monkeypatch):
        calls = []

        def fake(cmd, text=True):
            calls.append(cmd)
            with open(cmd[-1], "wb") as fh:
                fh.write(b"fake-webp-bytes")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(slides, "run", fake)
        return calls

    def test_writes_a_slide_and_prints_ok(self, tmp_path, monkeypatch, capsys):
        folder = self._setup(tmp_path, monkeypatch)
        self._fake_ffmpeg_writes(monkeypatch)
        cmd_extract(_mkargs(dir=str(folder), pick=["5.5=my-slide"], quality=82, crop="full"))
        out = capsys.readouterr().out
        assert "OK" in out
        assert (folder / "slides" / "0005-my-slide.webp").exists()

    def test_never_overwrites_an_existing_slide(self, tmp_path, monkeypatch, capsys):
        folder = self._setup(tmp_path, monkeypatch)
        self._fake_ffmpeg_writes(monkeypatch)
        cmd_extract(_mkargs(dir=str(folder), pick=["5.5=my-slide"], quality=82, crop="full"))
        capsys.readouterr()
        cmd_extract(_mkargs(dir=str(folder), pick=["5.5=my-slide"], quality=82, crop="full"))
        out = capsys.readouterr().out
        assert "SKIPPED" in out

    def test_crop_is_passed_through_to_ffmpeg(self, tmp_path, monkeypatch):
        folder = self._setup(tmp_path, monkeypatch)
        calls = self._fake_ffmpeg_writes(monkeypatch)
        cmd_extract(_mkargs(dir=str(folder), pick=["5.5=x"], quality=82, crop="10,20,0,0"))
        assert any("crop=10:20:0:0" in " ".join(c) for c in calls)


class TestCmdTile:
    def test_writes_four_reading_crops_per_slide(self, tmp_path, monkeypatch, capsys):
        _fake_tools(monkeypatch)
        folder = write_source(tmp_path)
        monkeypatch.setenv(slides.WORKROOT_ENV, str(tmp_path / "workroot"))
        slides_dir = folder / "slides"
        slides_dir.mkdir()
        (slides_dir / "0005-a.webp").write_bytes(b"x")

        def fake(cmd, text=True):
            with open(cmd[-1], "wb") as fh:
                fh.write(b"x")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(slides, "run", fake)
        cmd_tile(_mkargs(dir=str(folder), slide=None))
        out = capsys.readouterr().out
        assert out.count("TILE=") == 4

    def test_grabs_a_single_frame_so_an_animated_source_does_not_choke_ffmpeg(self, tmp_path, monkeypatch):
        """Without -frames:v 1, ffmpeg happily decodes every frame of an
        animated GIF (which add-shot accepts) and refuses to write them all
        to one .jpg -- add-shot's own accepted formats include .gif, so tile
        must cap it to one frame like every other single-frame grab in this
        script does."""
        _fake_tools(monkeypatch)
        folder = write_source(tmp_path)
        monkeypatch.setenv(slides.WORKROOT_ENV, str(tmp_path / "workroot"))
        slides_dir = folder / "slides"
        slides_dir.mkdir()
        (slides_dir / "0005-a.webp").write_bytes(b"x")

        calls = []

        def fake(cmd, text=True):
            calls.append(cmd)
            with open(cmd[-1], "wb") as fh:
                fh.write(b"x")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(slides, "run", fake)
        cmd_tile(_mkargs(dir=str(folder), slide=None))
        assert all("-frames:v" in c and "1" in c[c.index("-frames:v") + 1] for c in calls)

    def test_die_message_includes_ffmpeg_stderr(self, tmp_path, monkeypatch):
        _fake_tools(monkeypatch)
        folder = write_source(tmp_path)
        monkeypatch.setenv(slides.WORKROOT_ENV, str(tmp_path / "workroot"))
        slides_dir = folder / "slides"
        slides_dir.mkdir()
        (slides_dir / "0005-a.webp").write_bytes(b"x")

        monkeypatch.setattr(
            slides, "run",
            lambda cmd, text=True: SimpleNamespace(returncode=1, stdout="", stderr="a very specific ffmpeg gripe"),
        )
        with pytest.raises(SystemExit) as exc:
            cmd_tile(_mkargs(dir=str(folder), slide=None))
        assert "a very specific ffmpeg gripe" in str(exc.value)


class TestCmdLayout:
    def test_uses_default_percentages_and_prints_crop_help(self, tmp_path, monkeypatch, capsys):
        _fake_tools(monkeypatch)
        folder = write_source(tmp_path)
        monkeypatch.setenv(slides.WORKROOT_ENV, str(tmp_path / "workroot"))
        work = workdir(VID)
        with open(os.path.join(work, "video.mp4"), "wb") as fh:
            fh.write(b"fake")

        fake = DetectFakeRun(change_times=[], dur=100.0, hash_by_ss={})
        monkeypatch.setattr(slides, "run", fake)
        cmd_layout(_mkargs(dir=str(folder), at=None))
        out = capsys.readouterr().out
        assert "W,H,X,Y" in out
        assert "SHEET=" in out


class TestCmdAddShot:
    def test_copies_the_file_byte_for_byte(self, tmp_path, monkeypatch):
        folder = write_source(tmp_path)
        src = tmp_path / "shot.png"
        src.write_bytes(b"\x89PNG-fake-bytes")
        cmd_add_shot(_mkargs(dir=str(folder), file=str(src), at="4:56", name="human memory"))
        dst = folder / "slides" / "0456-human-memory.png"
        assert dst.read_bytes() == b"\x89PNG-fake-bytes"

    def test_refuses_to_overwrite(self, tmp_path):
        folder = write_source(tmp_path)
        src = tmp_path / "shot.png"
        src.write_bytes(b"one")
        cmd_add_shot(_mkargs(dir=str(folder), file=str(src), at="0:01", name="x"))
        with pytest.raises(SystemExit):
            cmd_add_shot(_mkargs(dir=str(folder), file=str(src), at="0:01", name="x"))

    def test_rejects_a_non_image_file(self, tmp_path):
        folder = write_source(tmp_path)
        src = tmp_path / "notes.txt"
        src.write_text("hi", encoding="utf-8")
        with pytest.raises(SystemExit):
            cmd_add_shot(_mkargs(dir=str(folder), file=str(src), at="0:01", name="x"))


# ---------------------------------------------------------------------------
# Offline integration test: a real, tiny synthetic video through real ffmpeg.
# ---------------------------------------------------------------------------

FFMPEG_MISSING = _shutil.which("ffmpeg") is None or _shutil.which("ffprobe") is None


def _draw_slide(path, bg, box, x, y):
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c={bg}:s=320x180:d=1",
         "-vf", f"drawbox=x={x}:y={y}:w=60:h=60:color={box}:t=fill",
         "-frames:v", "1", "-y", str(path)],
        check=True,
    )


def _draw_text_segment(path, title, bullets, dur):
    """A `dur`-second white slide in the same drawtext template two different
    talks would share -- a title line plus a couple of bullets -- so a dedupe
    regression can vary only the words, never the layout. Rendered as its own
    short clip (not a still stretched by the concat demuxer's ``duration``
    directive, which drops a short list's final entry) so two of these concat
    to an exact, known-length video."""
    font = find_font()
    if not font:
        pytest.skip("no monospace font on FONT_CANDIDATES to render text slides with")
    escaped_font = font.replace(":", "\\:")
    draws = []
    y = 100
    for size, text in [(60, title)] + [(36, b) for b in bullets]:
        escaped_text = text.replace(":", "\\:").replace("'", "\\'")
        draws.append(
            f"drawtext=fontfile='{escaped_font}':text='{escaped_text}':x=80:y={y}:"
            f"fontsize={size}:fontcolor=black"
        )
        y += size + 40
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c=white:s=1280x720:d={dur}:r=10",
         "-vf", ",".join(draws), "-pix_fmt", "yuv420p", "-y", str(path)],
        check=True,
    )


def _build_text_slides_video(build_dir):
    """Two 3s slides built from the same title-plus-bullets template, words
    only differing -- the shape a real talk's slide deck actually takes."""
    one, two = build_dir / "one.mp4", build_dir / "two.mp4"
    _draw_text_segment(one, "Why agents forget things",
                        ["bullet one about memory", "bullet two about context"], dur=3)
    _draw_text_segment(two, "Context management layer",
                        ["bullet one about routing", "bullet two about caching"], dur=3)

    listing = build_dir / "list.txt"
    listing.write_text("file 'one.mp4'\nfile 'two.mp4'\n", encoding="utf-8")
    out = build_dir / "text.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-r", "10", "-vsync", "cfr", "-pix_fmt", "yuv420p", "-y", str(out)],
        check=True, cwd=build_dir,
    )
    return out


@pytest.mark.skipif(FFMPEG_MISSING, reason="ffmpeg/ffprobe not on PATH")
def test_thumbnail_hash_does_not_dedupe_two_different_text_slides_sharing_a_template(tmp_path):
    """Two slides built from the same title-plus-bullets template, with
    completely different words, must hash far enough apart that dedupe()
    never calls them a duplicate -- that would hide real content behind a
    DUP line and drop it from the contact sheets. Goes straight at the
    dHash/dedupe pair with real ffmpeg-extracted frames, rather than through
    `detect`'s scene-change filter (a separate mechanism, covered elsewhere,
    whose sensitivity to a mostly-static text slide is not what this bug is
    about)."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    video_path = _build_text_slides_video(build_dir)

    # Re-resolve the real ffmpeg binary: an earlier faked-tools test may have
    # cached "/fake/ffmpeg" in the module-level TOOLS dict, which cmd_detect
    # would normally refresh with its own need("ffmpeg") call before this.
    slides.need("ffmpeg")
    h1 = slides.thumbnail_hash(str(video_path), 1.5)
    h2 = slides.thumbnail_hash(str(video_path), 4.5)

    assert dedupe([h1, h2], threshold=slides.DEFAULT_DUP_THRESHOLD) == [None, None], (
        f"distinct same-template slides collided as a dup (hamming={hamming(h1, h2)})"
    )


def _build_synthetic_video(build_dir):
    """A ~36s static-slide video: A, B, a 1s flash, a repeat of B, a
    uniformly dimmed copy of B, and D -- with known cut times."""
    a, b, c, bdim, d = (build_dir / n for n in ("A.png", "B.png", "C.png", "Bdim.png", "D.png"))
    _draw_slide(a, "0xC8C8C8", "0x323232", 20, 20)
    _draw_slide(b, "0xC8C8C8", "0x323232", 200, 20)
    _draw_slide(c, "0xC8C8C8", "0x323232", 20, 100)
    _draw_slide(bdim, "0x646464", "0x191919", 200, 20)  # exactly half of B's colors
    _draw_slide(d, "0xC8C8C8", "0x323232", 200, 100)

    listing = build_dir / "list.txt"
    listing.write_text(
        "file 'A.png'\nduration 7\n"
        "file 'B.png'\nduration 7\n"
        "file 'C.png'\nduration 1\n"
        "file 'B.png'\nduration 7\n"
        "file 'Bdim.png'\nduration 7\n"
        "file 'D.png'\nduration 7\n",
        encoding="utf-8",
    )
    out = build_dir / "out.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-r", "10", "-vsync", "cfr", "-pix_fmt", "yuv420p", "-y", str(out)],
        check=True, cwd=build_dir,
    )
    return out


@pytest.mark.skipif(FFMPEG_MISSING, reason="ffmpeg/ffprobe not on PATH")
def test_detect_dedupes_a_repeat_and_a_dimmed_copy_and_extract_never_overwrites(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(slides.WORKROOT_ENV, str(tmp_path / "workroot"))
    folder = write_source(tmp_path, vid="ABCDEFGHIJK", slug="synth-talk")

    build_dir = tmp_path / "build"
    build_dir.mkdir()
    synthetic = _build_synthetic_video(build_dir)

    work = workdir("ABCDEFGHIJK")
    _shutil.copyfile(synthetic, os.path.join(work, "video.mp4"))

    args = _mkargs(dir=str(folder), crop="full", threshold=0.04, merge=2.5, settle=1.5,
                   fps=2, sparse=180, dup_threshold=14, keep_dups=False)
    cmd_detect(args)
    out = capsys.readouterr().out

    tsv = open(os.path.join(work, "samples.tsv"), encoding="utf-8").read().splitlines()[1:]
    rows = [dict(zip(["index", "first", "sample_seconds", "sample_clock", "dup_of"], r.split("\t")))
            for r in tsv]

    def group_in(lo, hi):
        return next(r for r in rows if lo <= float(r["sample_seconds"]) < hi)

    b_original = group_in(7.0, 14.0)
    # The changes at 14s (the flash's own onset) and 15s (its return to B)
    # are within `merge` of each other, so this is one merged group, not a
    # second, separate repeat of B -- its own dHash still lands on B's, since
    # its sample is taken after the flash has already ended.
    flash_and_return_to_b = group_in(15.0, 22.0)
    b_dimmed = group_in(22.0, 29.0)
    slide_a = group_in(0.0, 7.0)
    slide_d = group_in(29.0, 36.0)

    assert b_original["dup_of"] == ""
    assert flash_and_return_to_b["dup_of"] == b_original["index"]
    assert b_dimmed["dup_of"] == b_original["index"]
    assert slide_a["dup_of"] == ""
    assert slide_d["dup_of"] == ""
    # The flash itself must not pass through silently: a merged group this
    # short (n=2, spanning under a second) gets a BURST cue naming its own
    # index, so re-sampling it (and so recovering the flash's own frame) is
    # one copy-pasted command away rather than requiring the reader to notice
    # its absence from the sheets.
    assert f"BURST {flash_and_return_to_b['index']} " in out

    extract_args = _mkargs(dir=str(folder), pick=[f"{b_original['sample_seconds']}=test-slide"],
                            quality=82, crop="full")
    cmd_extract(extract_args)
    dst = folder / "slides" / f"{stamp(float(b_original['sample_seconds']))}-test-slide.webp"
    assert dst.exists() and dst.stat().st_size > 0
    size_after_first = dst.stat().st_size

    cmd_extract(extract_args)  # must refuse to overwrite
    assert dst.stat().st_size == size_after_first
