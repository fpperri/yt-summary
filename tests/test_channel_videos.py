import pytest

import channel_videos as cv
import yt_transcript as yt
from channel_videos import apply_date_window, flat_list, list_channel, normalize_channel_url


def ytdlp_result(returncode=0, stdout="", stderr=""):
    import subprocess
    return subprocess.CompletedProcess(["yt-dlp"], returncode, stdout=stdout, stderr=stderr)


class TestNormalizeChannelUrl:
    @pytest.mark.parametrize("source", [
        "@somechannel",
        "https://www.youtube.com/@somechannel",
        "www.youtube.com/@somechannel",
        "https://www.youtube.com/@somechannel/",
        "https://www.youtube.com/@somechannel/videos",
        "https://www.youtube.com/@somechannel/shorts",
    ])
    def test_all_forms_collapse_to_one_base(self, source):
        assert normalize_channel_url(source) == "https://www.youtube.com/@somechannel"

    def test_keeps_channel_id_urls(self):
        url = "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv"
        assert normalize_channel_url(url) == url

    def test_empty_is_rejected(self):
        with pytest.raises(ValueError):
            normalize_channel_url("   ")

    def test_a_handle_named_after_a_tab_is_not_eaten(self):
        """Stripping trailing tabs unconditionally would turn the handle
        '@videos' into the bare youtube.com root."""
        assert normalize_channel_url("@videos") == "https://www.youtube.com/@videos"
        assert normalize_channel_url("@shorts") == "https://www.youtube.com/@shorts"

    def test_bare_handle_with_a_tab_suffix_is_stripped(self):
        assert normalize_channel_url("@somechannel/videos") == "https://www.youtube.com/@somechannel"

    @pytest.mark.parametrize("source", [
        "youtube.com/@chan",
        "http://youtube.com/@chan",
        "http://www.youtube.com/@chan",
        "http://m.youtube.com/@chan",
        "https://m.youtube.com/@chan",
    ])
    def test_normalizes_host_variants(self, source):
        """A manifest mixing youtube.com/www.youtube.com/m.youtube.com for the
        same channel must dedupe to one URL, so every host collapses to
        https://www.youtube.com."""
        assert normalize_channel_url(source) == "https://www.youtube.com/@chan"

    def test_host_normalization_never_touches_path_case(self):
        """/channel/UC... ids are case-sensitive; only the host may be rewritten."""
        url = "http://m.youtube.com/channel/UCAbCdEfGhIjKlMnOpQrStUv"
        assert normalize_channel_url(url) == "https://www.youtube.com/channel/UCAbCdEfGhIjKlMnOpQrStUv"


class TestFlatList:
    def test_parses_id_channel_and_title(self, monkeypatch):
        monkeypatch.setattr(
            cv.yt, "run_ytdlp",
            lambda args, timeout=None: ytdlp_result(stdout="aaaaaaaaaaa\tSome Channel\ta title\n"),
        )
        videos, error = flat_list("https://www.youtube.com/@c/videos")
        assert error is None
        assert videos == [{"video_id": "aaaaaaaaaaa", "title": "a title", "channel": "Some Channel"}]

    def test_na_channel_becomes_empty_string(self, monkeypatch):
        monkeypatch.setattr(
            cv.yt, "run_ytdlp",
            lambda args, timeout=None: ytdlp_result(stdout="aaaaaaaaaaa\tNA\ta title\n"),
        )
        videos, _ = flat_list("https://www.youtube.com/@c/videos")
        assert videos[0]["channel"] == ""

    def test_a_tab_inside_the_title_survives_the_split(self, monkeypatch):
        """The print template puts title last and flat_list splits with
        maxsplit, so a title containing a literal tab is not truncated."""
        monkeypatch.setattr(
            cv.yt, "run_ytdlp",
            lambda args, timeout=None: ytdlp_result(stdout="aaaaaaaaaaa\tSome Channel\tpart1\tpart2\n"),
        )
        videos, _ = flat_list("https://www.youtube.com/@c/videos")
        assert videos[0]["title"] == "part1\tpart2"

    def test_uses_run_ytdlp_for_the_encoding_flags(self, monkeypatch):
        """finding 7: every yt-dlp call in this module goes through
        yt.run_ytdlp so Windows doesn't silently drop non-cp1252 output."""
        calls = []
        monkeypatch.setattr(
            cv.yt, "run_ytdlp",
            lambda args, timeout=None: calls.append(args) or ytdlp_result(),
        )
        flat_list("https://www.youtube.com/@c/videos")
        (args,) = calls
        assert "--flat-playlist" in args
        assert "https://www.youtube.com/@c/videos" in args

    def test_limit_adds_playlist_end(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            cv.yt, "run_ytdlp",
            lambda args, timeout=None: calls.append(args) or ytdlp_result(),
        )
        flat_list("https://www.youtube.com/@c/videos", limit=100)
        (args,) = calls
        assert "--playlist-end" in args
        assert args[args.index("--playlist-end") + 1] == "100"

    def test_no_limit_means_no_playlist_end(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            cv.yt, "run_ytdlp",
            lambda args, timeout=None: calls.append(args) or ytdlp_result(),
        )
        flat_list("https://www.youtube.com/@c/videos")
        (args,) = calls
        assert "--playlist-end" not in args

    def test_nonzero_exit_reports_ytdlp_failure_message(self, monkeypatch):
        monkeypatch.setattr(
            cv.yt, "run_ytdlp",
            lambda args, timeout=None: ytdlp_result(returncode=1, stderr="HTTP Error 404: Not Found\n"),
        )
        videos, error = flat_list("https://www.youtube.com/@wrong/videos")
        assert videos == []
        assert "404" in error


class TestListChannel:
    def test_merges_tabs_and_dedupes(self, monkeypatch):
        """A video listed under both /videos and /shorts must appear once."""
        pages = {
            "https://www.youtube.com/@c/videos": [
                {"video_id": "aaaaaaaaaaa", "title": "long one"},
                {"video_id": "bbbbbbbbbbb", "title": "both tabs"},
            ],
            "https://www.youtube.com/@c/shorts": [
                {"video_id": "bbbbbbbbbbb", "title": "both tabs"},
                {"video_id": "ccccccccccc", "title": "a short"},
            ],
        }
        monkeypatch.setattr(cv, "flat_list", lambda url, limit=None: (pages.get(url, []), None))

        result = list_channel("https://www.youtube.com/@c")
        assert [v["video_id"] for v in result] == ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"]
        assert result[2]["tab"] == "shorts"

    def test_shorts_are_included(self, monkeypatch):
        """Shorts never appear under /videos, so a videos-only walk misses them."""
        monkeypatch.setattr(cv, "flat_list", lambda url, limit=None: (
            ([{"video_id": "ccccccccccc", "title": "a short"}], None)
            if url.endswith("/shorts") else ([], None)
        ))
        assert [v["video_id"] for v in list_channel("https://www.youtube.com/@c")] == ["ccccccccccc"]

    def test_a_channel_without_shorts_still_lists(self, monkeypatch):
        """The /shorts tab 404s for channels that have none. That is normal and
        must not fail the listing."""
        monkeypatch.setattr(cv, "flat_list", lambda url, limit=None: (
            ([{"video_id": "aaaaaaaaaaa", "title": "one"}], None)
            if url.endswith("/videos") else ([], "HTTP Error 404: Not Found")
        ))
        assert [v["video_id"] for v in list_channel("https://www.youtube.com/@c")] == ["aaaaaaaaaaa"]

    def test_reports_the_underlying_error_when_every_tab_fails(self, monkeypatch):
        """A wrong channel reference (display name instead of @handle) should say
        what yt-dlp said, not return an empty list."""
        monkeypatch.setattr(cv, "flat_list", lambda url, limit=None: ([], "HTTP Error 404: Not Found"))
        with pytest.raises(RuntimeError, match="404"):
            list_channel("https://www.youtube.com/@wrong")

    def test_passes_limit_per_tab_through_to_flat_list(self, monkeypatch):
        seen_limits = []
        monkeypatch.setattr(
            cv, "flat_list",
            lambda url, limit=None: (seen_limits.append(limit) or [], None),
        )
        list_channel("https://www.youtube.com/@c", limit_per_tab=100)
        assert seen_limits == [100, 100]


class TestChannelId:
    def test_reads_channel_id_from_the_flat_dump(self, monkeypatch):
        monkeypatch.setattr(
            cv.yt, "run_ytdlp",
            lambda args, timeout=None: ytdlp_result(stdout='{"channel_id": "UCabc123"}'),
        )
        assert cv.channel_id("https://www.youtube.com/@c") == "UCabc123"

    def test_raises_ytdlp_failure_on_nonzero_exit(self, monkeypatch):
        """finding 7: no more check=True — a classified exception instead of a
        bare CalledProcessError that drops stderr."""
        monkeypatch.setattr(
            cv.yt, "run_ytdlp",
            lambda args, timeout=None: ytdlp_result(returncode=1, stderr="HTTP Error 429: Too Many Requests\n"),
        )
        with pytest.raises(yt.RateLimited):
            cv.channel_id("https://www.youtube.com/@c")

    def test_raises_when_channel_id_is_missing(self, monkeypatch):
        monkeypatch.setattr(
            cv.yt, "run_ytdlp",
            lambda args, timeout=None: ytdlp_result(stdout='{}'),
        )
        with pytest.raises(RuntimeError, match="channel_id"):
            cv.channel_id("https://www.youtube.com/@c")


class TestProbeUploadDate:
    def test_returns_iso_date_on_success(self, monkeypatch):
        monkeypatch.setattr(
            cv.yt, "run_ytdlp",
            lambda args, timeout=None: ytdlp_result(stdout="20260830\n"),
        )
        assert cv.probe_upload_date("aaaaaaaaaaa") == "2026-08-30"

    def test_undatable_video_returns_empty_string(self, monkeypatch):
        """An unaired premiere: yt-dlp succeeds but prints no upload_date."""
        monkeypatch.setattr(
            cv.yt, "run_ytdlp",
            lambda args, timeout=None: ytdlp_result(stdout="NA\n"),
        )
        assert cv.probe_upload_date("aaaaaaaaaaa") == ""

    def test_raises_on_rate_limited_failure(self, monkeypatch):
        """finding 4: a throttled probe must not look like an undatable video,
        or apply_date_window will silently walk the whole channel history."""
        monkeypatch.setattr(
            cv.yt, "run_ytdlp",
            lambda args, timeout=None: ytdlp_result(returncode=1, stderr="HTTP Error 429: Too Many Requests\n"),
        )
        with pytest.raises(yt.RateLimited):
            cv.probe_upload_date("aaaaaaaaaaa")

    def test_raises_on_network_failure(self, monkeypatch):
        monkeypatch.setattr(
            cv.yt, "run_ytdlp",
            lambda args, timeout=None: ytdlp_result(returncode=1, stderr="urlopen error timed out\n"),
        )
        with pytest.raises(RuntimeError):
            cv.probe_upload_date("aaaaaaaaaaa")

    def test_other_failures_return_empty_string(self, monkeypatch):
        """A private/deleted video or a genuine premiere is not transient — the
        caller treats it as undatable, not as a reason to raise."""
        monkeypatch.setattr(
            cv.yt, "run_ytdlp",
            lambda args, timeout=None: ytdlp_result(returncode=1, stderr="ERROR: Private video\n"),
        )
        assert cv.probe_upload_date("aaaaaaaaaaa") == ""

    def test_uses_run_ytdlp(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            cv.yt, "run_ytdlp",
            lambda args, timeout=None: calls.append(args) or ytdlp_result(stdout="20260830\n"),
        )
        cv.probe_upload_date("aaaaaaaaaaa")
        (args,) = calls
        assert "aaaaaaaaaaa" in " ".join(args)


class TestWindowChannel:
    def test_lists_dates_and_windows_in_one_call(self, monkeypatch):
        monkeypatch.setattr(cv, "list_channel", lambda base_url, limit_per_tab=None, on_tab_error=None: [
            {"video_id": "aaaaaaaaaaa", "title": "t", "channel": "c", "tab": "videos"},
        ])
        monkeypatch.setattr(cv, "channel_id", lambda base_url: "UCabc")
        monkeypatch.setattr(cv, "rss_dates", lambda cid: {"aaaaaaaaaaa": "2026-08-30"})

        kept = cv.window_channel("https://www.youtube.com/@c", "2026-08-01")
        assert [v["video_id"] for v in kept] == ["aaaaaaaaaaa"]

    def test_passes_limit_per_tab_and_on_tab_error_to_list_channel(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(cv, "list_channel", lambda base_url, limit_per_tab=None, on_tab_error=None: (
            seen.update(limit_per_tab=limit_per_tab, on_tab_error=on_tab_error) or []
        ))
        monkeypatch.setattr(cv, "channel_id", lambda base_url: "UCabc")
        monkeypatch.setattr(cv, "rss_dates", lambda cid: {})

        marker = object()
        cv.window_channel("https://www.youtube.com/@c", "2026-08-01", limit_per_tab=42, on_tab_error=marker)
        assert seen == {"limit_per_tab": 42, "on_tab_error": marker}

    def test_rss_miss_falls_back_to_empty_dates_and_calls_on_rss_miss(self, monkeypatch):
        monkeypatch.setattr(cv, "list_channel", lambda base_url, limit_per_tab=None, on_tab_error=None: [
            {"video_id": "aaaaaaaaaaa", "title": "t", "channel": "c", "tab": "videos"},
        ])
        monkeypatch.setattr(cv, "channel_id", lambda base_url: "UCabc")

        def raise_rss(cid):
            raise RuntimeError("rss unreachable")
        monkeypatch.setattr(cv, "rss_dates", raise_rss)
        monkeypatch.setattr(cv, "probe_upload_date", lambda vid: "2026-08-30")

        misses = []
        kept = cv.window_channel("https://www.youtube.com/@c", "2026-08-01", on_rss_miss=misses.append)
        assert [v["video_id"] for v in kept] == ["aaaaaaaaaaa"]
        assert len(misses) == 1
        assert "rss unreachable" in str(misses[0])

    def test_list_channel_exceptions_propagate(self, monkeypatch):
        def raise_listing(base_url, limit_per_tab=None, on_tab_error=None):
            raise RuntimeError("could not list")
        monkeypatch.setattr(cv, "list_channel", raise_listing)

        with pytest.raises(RuntimeError, match="could not list"):
            cv.window_channel("https://www.youtube.com/@c", "2026-08-01")

    def test_probe_failure_propagates(self, monkeypatch):
        """finding 4: a rate-limited probe inside apply_date_window must not be
        swallowed here either."""
        monkeypatch.setattr(cv, "list_channel", lambda base_url, limit_per_tab=None, on_tab_error=None: [
            {"video_id": "aaaaaaaaaaa", "title": "t", "channel": "c", "tab": "videos"},
        ])
        monkeypatch.setattr(cv, "channel_id", lambda base_url: "UCabc")
        monkeypatch.setattr(cv, "rss_dates", lambda cid: {})

        def raise_rate_limited(vid):
            raise yt.RateLimited("HTTP Error 429: Too Many Requests")
        monkeypatch.setattr(cv, "probe_upload_date", raise_rate_limited)

        with pytest.raises(yt.RateLimited):
            cv.window_channel("https://www.youtube.com/@c", "2026-08-01")


class TestApplyDateWindow:
    def video(self, vid, title="t"):
        return {"video_id": vid, "title": title, "tab": "videos"}

    def test_keeps_only_videos_inside_the_window(self, monkeypatch):
        monkeypatch.setattr(cv, "probe_upload_date", lambda vid: pytest.fail("should not probe"))
        videos = [self.video("aaaaaaaaaaa"), self.video("bbbbbbbbbbb")]
        known = {"aaaaaaaaaaa": "2026-08-30", "bbbbbbbbbbb": "2026-01-01"}

        kept = apply_date_window(videos, "2026-08-01", known)
        assert [v["video_id"] for v in kept] == ["aaaaaaaaaaa"]
        assert kept[0]["published"] == "2026-08-30"

    def test_probes_videos_the_rss_feed_missed(self, monkeypatch):
        """RSS carries ~15 entries; a burst channel pushes recent uploads past it."""
        monkeypatch.setattr(cv, "probe_upload_date", lambda vid: "2026-08-20")
        videos = [self.video("aaaaaaaaaaa"), self.video("bbbbbbbbbbb")]
        known = {"aaaaaaaaaaa": "2026-08-30"}

        kept = apply_date_window(videos, "2026-08-01", known)
        assert {v["video_id"] for v in kept} == {"aaaaaaaaaaa", "bbbbbbbbbbb"}

    def test_stops_probing_after_a_run_of_old_videos(self, monkeypatch):
        probed = []

        def probe(vid):
            probed.append(vid)
            return "2020-01-01"

        monkeypatch.setattr(cv, "probe_upload_date", probe)
        videos = [self.video(f"{c}" * 11) for c in "abcdefghij"]

        apply_date_window(videos, "2026-08-01", {}, stop_after_old=3)
        assert len(probed) == 3

    def test_undated_videos_are_dropped_without_ending_the_walk(self, monkeypatch):
        """An unaired premiere has no upload_date. It should not be ingested, and it
        should not be read as 'we have reached the old videos' either."""
        dates = {"bbbbbbbbbbb": "2026-08-30"}
        monkeypatch.setattr(cv, "probe_upload_date", lambda vid: dates.get(vid, ""))
        videos = [self.video("aaaaaaaaaaa"), self.video("bbbbbbbbbbb")]

        kept = apply_date_window(videos, "2026-08-01", {}, stop_after_old=1)
        assert [v["video_id"] for v in kept] == ["bbbbbbbbbbb"]

    def test_early_stop_is_per_tab_so_shorts_survive(self, monkeypatch):
        """Regression: with one counter shared across the merged tab list, the
        walk breaks inside /videos — every channel has more than stop_after_old
        old uploads — and no short is ever examined."""
        monkeypatch.setattr(cv, "probe_upload_date", lambda vid: "")
        videos = [
            {"video_id": f"v{i:010d}", "title": f"v{i}", "tab": "videos"} for i in range(8)
        ] + [
            {"video_id": f"s{i:010d}", "title": f"s{i}", "tab": "shorts"} for i in range(3)
        ]
        known = {"v0000000000": "2026-08-30"}
        known.update({f"v{i:010d}": "2020-01-01" for i in range(1, 8)})
        known.update({f"s{i:010d}": "2026-08-29" for i in range(3)})

        kept = apply_date_window(videos, "2026-08-25", known, stop_after_old=5)
        assert [v["video_id"] for v in kept if v["tab"] == "shorts"] == [
            "s0000000000", "s0000000001", "s0000000002",
        ]
        assert "v0000000000" in [v["video_id"] for v in kept]

    def test_probes_are_paced(self, monkeypatch):
        """Probing is one request per video; unpaced it can trip the very rate
        limit the ingest phase is built to survive."""
        slept = []
        monkeypatch.setattr(cv, "probe_upload_date", lambda vid: "2026-08-30")
        monkeypatch.setattr(cv.time, "sleep", lambda s: slept.append(s))
        videos = [self.video(f"{c}" * 11) for c in "abc"]

        apply_date_window(videos, "2026-08-01", {}, pace=2.0)
        assert slept == [2.0, 2.0]  # between probes, not before the first

    def test_no_sleeping_when_rss_covered_everything(self, monkeypatch):
        monkeypatch.setattr(cv, "probe_upload_date", lambda vid: pytest.fail("should not probe"))
        monkeypatch.setattr(cv.time, "sleep", lambda s: pytest.fail("should not sleep"))
        videos = [self.video("aaaaaaaaaaa")]
        apply_date_window(videos, "2026-08-01", {"aaaaaaaaaaa": "2026-08-30"}, pace=5.0)

    def test_result_is_newest_first(self, monkeypatch):
        monkeypatch.setattr(cv, "probe_upload_date", lambda vid: "")
        videos = [self.video("aaaaaaaaaaa"), self.video("bbbbbbbbbbb"), self.video("ccccccccccc")]
        known = {
            "aaaaaaaaaaa": "2026-08-10",
            "bbbbbbbbbbb": "2026-08-30",
            "ccccccccccc": "2026-08-20",
        }
        kept = apply_date_window(videos, "2026-08-01", known)
        assert [v["published"] for v in kept] == ["2026-08-30", "2026-08-20", "2026-08-10"]
