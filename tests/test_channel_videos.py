import io
import json

import pytest

import channel_videos as cv
import yt_transcript as yt
from channel_videos import apply_date_window, flat_list, list_channel, normalize_channel_url


def ytdlp_result(returncode=0, stdout="", stderr=""):
    import subprocess
    return subprocess.CompletedProcess(["yt-dlp"], returncode, stdout=stdout, stderr=stderr)


def atom_feed(dates: dict) -> bytes:
    """A minimal RSS/Atom body in the shape ``rss_dates`` parses: one
    ``<entry>`` per {video_id: 'YYYY-MM-DD'} pair."""
    entries = "\n".join(
        f'<entry><yt:videoId>{vid}</yt:videoId>'
        f'<published>{date}T12:00:00+00:00</published></entry>'
        for vid, date in dates.items()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
        f"{entries}</feed>"
    ).encode("utf-8")


class FakeYtDlp:
    """Answers the three yt-dlp shapes ``window_channel`` drives — a tab's
    flat-playlist listing, the ``-J`` channel dump ``channel_id`` reads, and a
    per-video upload-date probe — from canned data, and records every
    argument list it was called with.

    ``tabs`` maps a tab name to its raw (pre-dedupe, pre-cap) listing as
    ``[(video_id, title), ...]``; a ``--playlist-end`` in the call args slices
    it, the same way yt-dlp itself would stop early. ``dates`` maps a video id
    to an 8-digit upload_date string, or to ``"RATE_LIMITED"`` to make that
    probe fail as a throttled request. ``listing_errors`` maps a tab name to a
    stderr message that makes its listing fail outright. ``channel_id_error``
    makes the ``-J`` dump fail, the same way a bad channel reference would.
    """

    def __init__(self, channel_id="UCabc123", tabs=None, dates=None,
                listing_errors=None, channel_id_error=None):
        self.channel_id = channel_id
        self.tabs = tabs or {}
        self.dates = dates or {}
        self.listing_errors = listing_errors or {}
        self.channel_id_error = channel_id_error
        self.calls = []

    def __call__(self, args, timeout=None):
        self.calls.append(args)
        if "-J" in args:
            if self.channel_id_error:
                return ytdlp_result(returncode=1, stderr=self.channel_id_error)
            return ytdlp_result(stdout=json.dumps({"channel_id": self.channel_id}))

        if "--flat-playlist" in args:
            tab = "shorts" if args[-1].endswith("/shorts") else "videos"
            if tab in self.listing_errors:
                return ytdlp_result(returncode=1, stderr=self.listing_errors[tab])
            entries = self.tabs.get(tab, [])
            if "--playlist-end" in args:
                cap = int(args[args.index("--playlist-end") + 1])
                entries = entries[:cap]
            lines = "".join(f"{vid}\tSome Channel\t{title}\n" for vid, title in entries)
            return ytdlp_result(stdout=lines)

        video_id = args[-1].rsplit("=", 1)[-1]
        date = self.dates.get(video_id)
        if date is None:
            return ytdlp_result(returncode=1, stderr="ERROR: Private video")
        if date == "RATE_LIMITED":
            return ytdlp_result(returncode=1, stderr="HTTP Error 429: Too Many Requests")
        return ytdlp_result(stdout=date + "\n")


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

    def test_zero_limit_also_means_no_playlist_end(self, monkeypatch):
        """A falsy limit means unlimited everywhere, so 0 and None must behave
        the same way here."""
        calls = []
        monkeypatch.setattr(
            cv.yt, "run_ytdlp",
            lambda args, timeout=None: calls.append(args) or ytdlp_result(),
        )
        flat_list("https://www.youtube.com/@c/videos", limit=0)
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
        """A throttled probe must not look like an undatable video, or
        apply_date_window will silently walk the whole channel history."""
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
        with pytest.raises(RuntimeError) as raised:
            cv.probe_upload_date("aaaaaaaaaaa")
        assert cv.yt.failure_kind(raised.value) == "network"

    def test_a_permanent_http_error_is_undatable_not_raised(self, monkeypatch):
        monkeypatch.setattr(
            cv.yt, "run_ytdlp",
            lambda args, timeout=None: ytdlp_result(
                returncode=1, stderr="ERROR: [youtube] aaaaaaaaaaa: Unable to download API page: "
                                     "HTTP Error 404: Not Found\n"),
        )
        assert cv.probe_upload_date("aaaaaaaaaaa") == ""

    def test_other_failures_return_empty_string(self, monkeypatch):
        """A private/deleted video or a genuine premiere is not transient — the
        caller treats it as undatable, not as a reason to raise."""
        monkeypatch.setattr(
            cv.yt, "run_ytdlp",
            lambda args, timeout=None: ytdlp_result(returncode=1, stderr="ERROR: Private video\n"),
        )
        assert cv.probe_upload_date("aaaaaaaaaaa") == ""


class TestWindowChannel:
    """Exercises window_channel end to end, faking only its process/network
    boundaries: yt.run_ytdlp (via FakeYtDlp) and urllib.request.urlopen for
    the RSS feed. list_channel, channel_id, rss_dates and probe_upload_date
    stay real, so a test only passes if the whole chain actually works."""

    def fake_urlopen(self, dates):
        return lambda url, context=None, timeout=None: io.BytesIO(atom_feed(dates))

    def test_lists_dates_and_windows_in_one_call(self, monkeypatch):
        fake = FakeYtDlp(tabs={
            "videos": [("aaaaaaaaaaa", "t1"), ("bbbbbbbbbbb", "t2")],
            "shorts": [("ccccccccccc", "t3")],
        })
        monkeypatch.setattr(cv.yt, "run_ytdlp", fake)
        monkeypatch.setattr(cv.urllib.request, "urlopen", self.fake_urlopen({
            "aaaaaaaaaaa": "2026-08-30", "bbbbbbbbbbb": "2026-01-15", "ccccccccccc": "2026-08-25",
        }))

        kept = cv.window_channel("https://www.youtube.com/@c", "2026-08-01")
        assert [v["video_id"] for v in kept] == ["aaaaaaaaaaa", "ccccccccccc"]

    def test_the_cap_reaches_ytdlp_as_playlist_end_and_zero_sends_none(self, monkeypatch):
        capped = FakeYtDlp(tabs={"videos": [("aaaaaaaaaaa", "t1"), ("bbbbbbbbbbb", "t2")]})
        monkeypatch.setattr(cv.yt, "run_ytdlp", capped)
        monkeypatch.setattr(cv.urllib.request, "urlopen", self.fake_urlopen({}))
        cv.window_channel("https://www.youtube.com/@c", "2026-08-01", limit_per_tab=1)
        listing_calls = [c for c in capped.calls if "--flat-playlist" in c and "-J" not in c]
        assert any("--playlist-end" in c and c[c.index("--playlist-end") + 1] == "1"
                   for c in listing_calls)

        uncapped = FakeYtDlp(tabs={"videos": [("aaaaaaaaaaa", "t1")]})
        monkeypatch.setattr(cv.yt, "run_ytdlp", uncapped)
        monkeypatch.setattr(cv.urllib.request, "urlopen", self.fake_urlopen({}))
        cv.window_channel("https://www.youtube.com/@c", "2026-08-01", limit_per_tab=0)
        assert not any("--playlist-end" in c for c in uncapped.calls)

    def test_rss_miss_falls_back_to_probing_and_calls_on_rss_miss(self, monkeypatch):
        """Covers a channel_id failure the same way a broken RSS lookup does:
        window_channel treats both as reasons to date every video by hand."""
        fake = FakeYtDlp(tabs={"videos": [("aaaaaaaaaaa", "t1")]},
                        dates={"aaaaaaaaaaa": "20260830"},
                        channel_id_error="ERROR: could not resolve channel")
        monkeypatch.setattr(cv.yt, "run_ytdlp", fake)

        misses = []
        kept = cv.window_channel("https://www.youtube.com/@c", "2026-08-01", on_rss_miss=misses.append)
        assert [v["video_id"] for v in kept] == ["aaaaaaaaaaa"]
        assert len(misses) == 1
        assert "could not resolve channel" in str(misses[0])

    def test_a_failed_rss_fetch_falls_back_to_probing(self, monkeypatch):
        fake = FakeYtDlp(tabs={"videos": [("aaaaaaaaaaa", "t1")]}, dates={"aaaaaaaaaaa": "20260830"})
        monkeypatch.setattr(cv.yt, "run_ytdlp", fake)

        def unreachable_feed(url, context=None, timeout=None):
            raise OSError("CERTIFICATE_VERIFY_FAILED")

        monkeypatch.setattr(cv.urllib.request, "urlopen", unreachable_feed)

        misses = []
        kept = cv.window_channel("https://www.youtube.com/@c", "2026-08-01", on_rss_miss=misses.append)
        assert [(v["video_id"], v["published"]) for v in kept] == [("aaaaaaaaaaa", "2026-08-30")]
        assert "CERTIFICATE_VERIFY_FAILED" in str(misses[0])

    def test_listing_failure_on_every_tab_propagates(self, monkeypatch):
        fake = FakeYtDlp(listing_errors={
            "videos": "HTTP Error 404: Not Found", "shorts": "HTTP Error 404: Not Found",
        })
        monkeypatch.setattr(cv.yt, "run_ytdlp", fake)

        with pytest.raises(RuntimeError, match="404"):
            cv.window_channel("https://www.youtube.com/@c", "2026-08-01")

    def test_a_rate_limited_probe_propagates(self, monkeypatch):
        """A rate-limited probe inside apply_date_window must not be swallowed
        here either."""
        fake = FakeYtDlp(tabs={"videos": [("aaaaaaaaaaa", "t1")]},
                        dates={"aaaaaaaaaaa": "RATE_LIMITED"})
        monkeypatch.setattr(cv.yt, "run_ytdlp", fake)
        monkeypatch.setattr(cv.urllib.request, "urlopen", self.fake_urlopen({}))

        with pytest.raises(yt.RateLimited):
            cv.window_channel("https://www.youtube.com/@c", "2026-08-01")

    def test_cap_hit_fires_when_a_tabs_raw_listing_was_capped_and_its_tail_is_still_in_window(self, monkeypatch):
        fake = FakeYtDlp(tabs={"videos": [("aaaaaaaaaaa", "t1"), ("bbbbbbbbbbb", "t2")]})
        monkeypatch.setattr(cv.yt, "run_ytdlp", fake)
        monkeypatch.setattr(cv.urllib.request, "urlopen", self.fake_urlopen({
            "aaaaaaaaaaa": "2026-08-30", "bbbbbbbbbbb": "2026-08-20",
        }))

        hits = []
        cv.window_channel("https://www.youtube.com/@c", "2026-08-01", limit_per_tab=2,
                          on_cap_hit=lambda tab, limit: hits.append((tab, limit)))
        assert hits == [("videos", 2)]

    def test_cap_hit_does_not_fire_when_the_tab_listed_fewer_than_the_cap(self, monkeypatch):
        fake = FakeYtDlp(tabs={"videos": [("aaaaaaaaaaa", "t1"), ("bbbbbbbbbbb", "t2")]})
        monkeypatch.setattr(cv.yt, "run_ytdlp", fake)
        monkeypatch.setattr(cv.urllib.request, "urlopen", self.fake_urlopen({
            "aaaaaaaaaaa": "2026-08-30", "bbbbbbbbbbb": "2026-08-20",
        }))

        hits = []
        cv.window_channel("https://www.youtube.com/@c", "2026-08-01", limit_per_tab=5,
                          on_cap_hit=lambda tab, limit: hits.append((tab, limit)))
        assert hits == []

    def test_cap_hit_does_not_fire_when_the_capped_tail_falls_outside_the_window(self, monkeypatch):
        """The tab was cut off at the cap, but its oldest listed video is
        already too old — there is no reason to believe more in-window videos
        were dropped beyond it."""
        fake = FakeYtDlp(tabs={"videos": [("aaaaaaaaaaa", "t1"), ("bbbbbbbbbbb", "t2")]})
        monkeypatch.setattr(cv.yt, "run_ytdlp", fake)
        monkeypatch.setattr(cv.urllib.request, "urlopen", self.fake_urlopen({
            "aaaaaaaaaaa": "2026-08-30", "bbbbbbbbbbb": "2026-01-01",
        }))

        hits = []
        cv.window_channel("https://www.youtube.com/@c", "2026-08-01", limit_per_tab=2,
                          on_cap_hit=lambda tab, limit: hits.append((tab, limit)))
        assert hits == []


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
