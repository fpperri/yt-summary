import sweep as sweep_module
from sweep import read_manifest, sweep


class TestReadManifest:
    def test_reads_channels_ignoring_comments_and_blanks(self, tmp_path):
        manifest = tmp_path / "channels.txt"
        manifest.write_text(
            "# my channels\n"
            "@alpha\n"
            "\n"
            "https://www.youtube.com/@beta   # trailing note\n"
            "   \n",
            encoding="utf-8",
        )
        assert read_manifest(manifest) == [
            "https://www.youtube.com/@alpha",
            "https://www.youtube.com/@beta",
        ]

    def test_dedupes_equivalent_forms_of_one_channel(self, tmp_path):
        """The same channel written three ways should be polled once."""
        manifest = tmp_path / "channels.txt"
        manifest.write_text(
            "@alpha\n"
            "https://www.youtube.com/@alpha\n"
            "https://www.youtube.com/@alpha/videos\n",
            encoding="utf-8",
        )
        assert read_manifest(manifest) == ["https://www.youtube.com/@alpha"]

    def test_empty_manifest(self, tmp_path):
        manifest = tmp_path / "channels.txt"
        manifest.write_text("# nothing here\n", encoding="utf-8")
        assert read_manifest(manifest) == []


class TestSweep:
    def manifest(self, tmp_path):
        path = tmp_path / "channels.txt"
        path.write_text("@alpha\n", encoding="utf-8")
        return path

    def stub_channel(self, monkeypatch, videos):
        monkeypatch.setattr(
            sweep_module.cv, "window_channel",
            lambda url, cutoff, stop_after_old=5, probe_pace=1.0, limit_per_tab=None,
                   on_tab_error=None, on_rss_miss=None, **kwargs: videos,
        )

    def test_dry_run_reports_without_ingesting(self, tmp_path, monkeypatch):
        """Recording calls (rather than raising inside the stub) catches the
        mistake even if a future dry-run path wraps ingest in a try/except."""
        self.stub_channel(monkeypatch, [{"video_id": "aaaaaaaaaaa", "title": "one", "tab": "videos"}])
        calls = []
        monkeypatch.setattr(sweep_module, "ingest", lambda *a, **k: calls.append((a, k)))

        result = sweep(self.manifest(tmp_path), tmp_path / "out", since_days=3650, dry_run=True)
        assert calls == []
        assert result["ingested"] == 0
        assert result["failed"] == 0
        assert [c["video_id"] for c in result["candidates"]] == ["aaaaaaaaaaa"]

    def test_ingests_candidates(self, tmp_path, monkeypatch):
        self.stub_channel(monkeypatch, [
            {"video_id": "aaaaaaaaaaa", "title": "one", "tab": "videos"},
            {"video_id": "bbbbbbbbbbb", "title": "two", "tab": "videos"},
        ])
        seen = []

        def fake_ingest(video_id, output_dir, hints=None, ingested=None):
            seen.append(video_id)
            return {"slug": f"s-{video_id}", "skipped": False, "path": "p"}

        monkeypatch.setattr(sweep_module, "ingest", fake_ingest)
        result = sweep(self.manifest(tmp_path), tmp_path / "out", since_days=3650, pace=0)

        assert result["ingested"] == 2
        assert seen == ["aaaaaaaaaaa", "bbbbbbbbbbb"]

    def test_already_ingested_videos_are_never_fetched(self, tmp_path, monkeypatch):
        """Idempotency: a second sweep over unchanged channels does no work."""
        self.stub_channel(monkeypatch, [{"video_id": "aaaaaaaaaaa", "title": "one", "tab": "videos"}])
        monkeypatch.setattr(sweep_module, "ingested_videos",
                            lambda d: {"aaaaaaaaaaa": tmp_path / "out" / "aaaaaaaaaaa"})
        monkeypatch.setattr(sweep_module, "ingest",
                            lambda *a, **k: pytest_fail_ingest())

        result = sweep(self.manifest(tmp_path), tmp_path / "out", since_days=3650, pace=0)
        assert result == {"ingested": 0, "skipped": 0, "failed": 0, "blocked": False,
                          "candidates": [], "channels_failed": 0, "channels_blocked": 0}

    def test_hints_from_the_candidate_are_passed_to_ingest(self, tmp_path, monkeypatch):
        """The listing already knows title/channel/published — no reason to
        make ingest() re-fetch metadata it was just handed."""
        self.stub_channel(monkeypatch, [
            {"video_id": "aaaaaaaaaaa", "title": "one", "channel": "Chan",
             "published": "2026-08-01", "tab": "videos"},
        ])
        seen_hints = []

        def fake_ingest(video_id, output_dir, hints=None, ingested=None):
            seen_hints.append(hints)
            return {"slug": "s", "skipped": False, "path": "p"}

        monkeypatch.setattr(sweep_module, "ingest", fake_ingest)
        sweep(self.manifest(tmp_path), tmp_path / "out", since_days=3650, pace=0)

        assert seen_hints == [{"title": "one", "channel": "Chan", "published": "2026-08-01"}]

    def test_unaired_premiere_is_skipped_not_failed(self, tmp_path, monkeypatch):
        self.stub_channel(monkeypatch, [{"video_id": "aaaaaaaaaaa", "title": "soon", "tab": "videos"}])

        def premiere(video_id, output_dir, hints=None, ingested=None):
            raise RuntimeError("ERROR: This live event will begin in 2 hours")

        monkeypatch.setattr(sweep_module, "ingest", premiere)
        result = sweep(self.manifest(tmp_path), tmp_path / "out", since_days=3650, pace=0)

        assert result["skipped"] == 1
        assert result["failed"] == 0

    def test_rate_limit_retries_the_same_video_then_gives_up(self, tmp_path, monkeypatch):
        self.stub_channel(monkeypatch, [{"video_id": "aaaaaaaaaaa", "title": "one", "tab": "videos"}])
        attempts = []

        def blocked(video_id, output_dir, hints=None, ingested=None):
            attempts.append(video_id)
            raise RuntimeError("HTTP Error 429: Too Many Requests")

        monkeypatch.setattr(sweep_module, "ingest", blocked)
        monkeypatch.setattr(sweep_module.time, "sleep", lambda s: None)

        result = sweep(self.manifest(tmp_path), tmp_path / "out",
                       since_days=3650, pace=0, max_block=3)

        assert attempts == ["aaaaaaaaaaa"] * 3  # retried the same id, never advanced past it
        assert result["ingested"] == 0

    def test_giving_up_rate_limited_is_reported_as_blocked(self, tmp_path, monkeypatch):
        """Otherwise a sweep that wasted hours achieving nothing is
        indistinguishable from one that found nothing new."""
        self.stub_channel(monkeypatch, [{"video_id": "aaaaaaaaaaa", "title": "one", "tab": "videos"}])

        def blocked(video_id, output_dir, hints=None, ingested=None):
            raise RuntimeError("HTTP Error 429: Too Many Requests")

        monkeypatch.setattr(sweep_module, "ingest", blocked)
        monkeypatch.setattr(sweep_module.time, "sleep", lambda s: None)

        result = sweep(self.manifest(tmp_path), tmp_path / "out",
                       since_days=3650, pace=0, max_block=2)
        assert result["blocked"] is True

    def test_a_clean_sweep_is_not_marked_blocked(self, tmp_path, monkeypatch):
        self.stub_channel(monkeypatch, [{"video_id": "aaaaaaaaaaa", "title": "one", "tab": "videos"}])
        monkeypatch.setattr(sweep_module, "ingest",
                            lambda v, o, hints=None, ingested=None:
                                {"slug": "s", "skipped": False, "path": "p"})
        result = sweep(self.manifest(tmp_path), tmp_path / "out", since_days=3650, pace=0)
        assert result["blocked"] is False

    def test_a_video_id_containing_429_is_not_a_rate_limit(self, tmp_path, monkeypatch):
        """Regression: video ids are 11 chars of [A-Za-z0-9_-], so one can contain
        '429'. A bare-429 match turned a permanently dead video into an infinite
        backoff loop that stalled the whole sweep for hours."""
        self.stub_channel(monkeypatch, [
            {"video_id": "a429bcdefgh", "title": "dead", "tab": "videos"},
            {"video_id": "bbbbbbbbbbb", "title": "fine", "tab": "videos"},
        ])
        attempts = []

        def ingest(video_id, output_dir, hints=None, ingested=None):
            attempts.append(video_id)
            if video_id == "a429bcdefgh":
                raise RuntimeError(
                    "transcript unavailable for a429bcdefgh: "
                    "api=NoTranscriptFound() ytdlp=RuntimeError('no vtt written')"
                )
            return {"slug": "s", "skipped": False, "path": "p"}

        monkeypatch.setattr(sweep_module, "ingest", ingest)
        monkeypatch.setattr(sweep_module.time, "sleep", lambda s: None)

        result = sweep(self.manifest(tmp_path), tmp_path / "out", since_days=3650, pace=0)
        assert attempts == ["a429bcdefgh", "bbbbbbbbbbb"]  # moved on, did not retry
        assert result["failed"] == 1 and result["ingested"] == 1
        assert result["blocked"] is False

    def test_network_failure_is_not_a_rate_limit_either(self, tmp_path, monkeypatch):
        """Classification comes from yt.failure_kind, which also recognises
        transport failures — those advance and fail exactly like any other
        non-retryable error, they just aren't mistaken for a 429."""
        self.stub_channel(monkeypatch, [{"video_id": "aaaaaaaaaaa", "title": "one", "tab": "videos"}])

        def network_error(video_id, output_dir, hints=None, ingested=None):
            raise RuntimeError("urlopen error timed out")

        monkeypatch.setattr(sweep_module, "ingest", network_error)
        monkeypatch.setattr(sweep_module.time, "sleep", lambda s: None)

        result = sweep(self.manifest(tmp_path), tmp_path / "out", since_days=3650, pace=0)
        assert result["failed"] == 1
        assert result["blocked"] is False

    def test_ordinary_failure_advances_to_the_next_video(self, tmp_path, monkeypatch):
        self.stub_channel(monkeypatch, [
            {"video_id": "aaaaaaaaaaa", "title": "one", "tab": "videos"},
            {"video_id": "bbbbbbbbbbb", "title": "two", "tab": "videos"},
        ])

        def fail_first(video_id, output_dir, hints=None, ingested=None):
            if video_id == "aaaaaaaaaaa":
                raise RuntimeError("transcripts are disabled")
            return {"slug": "s", "skipped": False, "path": "p"}

        monkeypatch.setattr(sweep_module, "ingest", fail_first)
        result = sweep(self.manifest(tmp_path), tmp_path / "out", since_days=3650, pace=0)

        assert result["failed"] == 1
        assert result["ingested"] == 1

    def test_a_failed_channel_is_counted_and_logged(self, tmp_path, monkeypatch):
        """A channel that could not be listed at all must not look like a
        clean sweep that simply had nothing new."""
        def raising_window_channel(url, cutoff, stop_after_old=5, probe_pace=1.0,
                                   limit_per_tab=None, on_tab_error=None, on_rss_miss=None,
                                   **kwargs):
            raise RuntimeError("ERROR: [youtube:tab] @alpha: This channel does not exist")

        monkeypatch.setattr(sweep_module.cv, "window_channel", raising_window_channel)
        result = sweep(self.manifest(tmp_path), tmp_path / "out", since_days=3650, pace=0)

        assert result["channels_failed"] == 1
        assert result["ingested"] == 0

    def test_a_rate_limited_channel_is_counted_as_blocked_and_logged(self, tmp_path, monkeypatch, capsys):
        """A channel walk can itself be rate-limited (the listing or the date
        probes get IP-blocked), not just an individual video's caption fetch —
        that must be visible as its own signal rather than folded into an
        ordinary channel failure."""
        def rate_limited_window_channel(url, cutoff, stop_after_old=5, probe_pace=1.0,
                                        limit_per_tab=None, on_tab_error=None, on_rss_miss=None,
                                        **kwargs):
            raise sweep_module.yt.RateLimited("HTTP Error 429: Too Many Requests")

        monkeypatch.setattr(sweep_module.cv, "window_channel", rate_limited_window_channel)
        result = sweep(self.manifest(tmp_path), tmp_path / "out", since_days=3650, pace=0)

        assert result["channels_blocked"] == 1
        assert result["channels_failed"] == 1
        out = capsys.readouterr().out
        assert "CHANNEL-FAIL" in out
        assert "rate limited" in out.lower()

    def test_a_429_in_a_plain_listing_error_still_counts_as_blocked(self, tmp_path, monkeypatch):
        """A listing that fails on every tab surfaces as a plain RuntimeError
        joining each tab's yt-dlp message, not as a typed RateLimited."""
        def failing_listing(url, cutoff, *args, **kwargs):
            raise RuntimeError("videos: ERROR: Unable to download webpage: HTTP Error 429: "
                               "Too Many Requests; shorts: ERROR: HTTP Error 429: Too Many Requests")

        monkeypatch.setattr(sweep_module.cv, "window_channel", failing_listing)
        result = sweep(self.manifest(tmp_path), tmp_path / "out", since_days=3650, pace=0)

        assert result["channels_blocked"] == 1
        assert sweep_module.exit_code(result) == 2

    def test_the_closing_line_names_failed_and_blocked_channels(self, tmp_path, monkeypatch, capsys):
        """A run that exits 2 only because every channel walk was blocked must
        not end on a line that reads like an idle, clean sweep."""
        def blocked(url, cutoff, *args, **kwargs):
            raise sweep_module.yt.RateLimited("HTTP Error 429: Too Many Requests")

        monkeypatch.setattr(sweep_module.cv, "window_channel", blocked)
        sweep(self.manifest(tmp_path), tmp_path / "out", since_days=3650, pace=0)

        closing = capsys.readouterr().out.strip().splitlines()[-1]
        assert "sweep end:" in closing
        assert "channels_failed=1" in closing
        assert "channels_blocked=1" in closing

    def test_cap_hit_on_a_channel_tab_is_logged(self, tmp_path, monkeypatch, capsys):
        """window_channel calls back on_cap_hit when a tab's listing was cut
        off with in-window videos possibly still beyond the cap — surfacing
        that as a log line is what tells a human to raise --max-listed rather
        than silently missing videos forever."""
        def capped_window_channel(url, cutoff, stop_after_old=5, probe_pace=1.0,
                                  limit_per_tab=None, on_tab_error=None, on_rss_miss=None,
                                  on_cap_hit=None, **kwargs):
            if on_cap_hit:
                on_cap_hit("shorts", 42)
            return []

        monkeypatch.setattr(sweep_module.cv, "window_channel", capped_window_channel)
        sweep(self.manifest(tmp_path), tmp_path / "out", since_days=3650, pace=0)

        out = capsys.readouterr().out
        assert "CAP-HIT" in out and "/shorts" in out and "42" in out and "--max-listed" in out

    def test_probe_error_rate_limited_keeps_already_dated_videos(self, tmp_path, monkeypatch, capsys):
        """A probe raising mid-walk must not throw away videos the walk had
        already dated before the block hit — only the walk itself stops."""
        def probing_window_channel(url, cutoff, stop_after_old=5, probe_pace=1.0,
                                   limit_per_tab=None, on_tab_error=None, on_rss_miss=None,
                                   on_cap_hit=None, on_probe_error=None, **kwargs):
            on_probe_error(sweep_module.yt.RateLimited("HTTP Error 429: Too Many Requests"))
            return [{"video_id": "aaaaaaaaaaa", "title": "one", "tab": "videos"}]

        monkeypatch.setattr(sweep_module.cv, "window_channel", probing_window_channel)
        result = sweep(self.manifest(tmp_path), tmp_path / "out", since_days=3650, dry_run=True)

        assert result["channels_failed"] == 1
        assert result["channels_blocked"] == 1
        assert [c["video_id"] for c in result["candidates"]] == ["aaaaaaaaaaa"]
        out = capsys.readouterr().out
        assert "CHANNEL-FAIL" in out and "rate limited" in out.lower()
        assert "keeping 1 video" in out

    def test_probe_error_network_is_not_counted_as_blocked(self, tmp_path, monkeypatch, capsys):
        def probing_window_channel(url, cutoff, stop_after_old=5, probe_pace=1.0,
                                   limit_per_tab=None, on_tab_error=None, on_rss_miss=None,
                                   on_cap_hit=None, on_probe_error=None, **kwargs):
            on_probe_error(RuntimeError("urlopen error timed out"))
            return []

        monkeypatch.setattr(sweep_module.cv, "window_channel", probing_window_channel)
        result = sweep(self.manifest(tmp_path), tmp_path / "out", since_days=3650, dry_run=True)

        assert result["channels_failed"] == 1
        assert result["channels_blocked"] == 0
        out = capsys.readouterr().out
        assert "CHANNEL-FAIL" in out
        assert "rate limited" not in out.lower()

    def test_rate_limited_tab_error_counts_channel_blocked(self, tmp_path, monkeypatch, capsys):
        """/videos 429ing while /shorts lists fine doesn't raise out of
        list_channel, but it is still an IP block and must count as one."""
        def half_blocked_window_channel(url, cutoff, stop_after_old=5, probe_pace=1.0,
                                        limit_per_tab=None, on_tab_error=None, on_rss_miss=None,
                                        on_cap_hit=None, on_probe_error=None, **kwargs):
            on_tab_error("videos", "HTTP Error 429: Too Many Requests")
            return [{"video_id": "aaaaaaaaaaa", "title": "short", "tab": "shorts"}]

        monkeypatch.setattr(sweep_module.cv, "window_channel", half_blocked_window_channel)
        result = sweep(self.manifest(tmp_path), tmp_path / "out", since_days=3650, dry_run=True)

        assert result["channels_failed"] == 1
        assert result["channels_blocked"] == 1
        assert [c["video_id"] for c in result["candidates"]] == ["aaaaaaaaaaa"]
        assert "TAB-FAIL" in capsys.readouterr().out

    def test_stops_polling_remaining_channels_once_one_is_blocked(self, tmp_path, monkeypatch, capsys):
        """A blocked IP stays hot across the rest of the manifest unless the
        walk stops polling as soon as the first channel comes back blocked."""
        manifest = tmp_path / "channels.txt"
        manifest.write_text("@alpha\n@beta\n@gamma\n", encoding="utf-8")

        def blocked_first(url, cutoff, stop_after_old=5, probe_pace=1.0, limit_per_tab=None,
                          on_tab_error=None, on_rss_miss=None, on_cap_hit=None,
                          on_probe_error=None, **kwargs):
            if url.endswith("alpha"):
                on_probe_error(sweep_module.yt.RateLimited("HTTP Error 429: Too Many Requests"))
                return [{"video_id": "aaaaaaaaaaa", "title": "one", "tab": "videos"}]
            raise AssertionError(f"must not poll {url} once alpha was blocked")

        monkeypatch.setattr(sweep_module.cv, "window_channel", blocked_first)
        result = sweep(manifest, tmp_path / "out", since_days=3650, dry_run=True)

        assert [c["video_id"] for c in result["candidates"]] == ["aaaaaaaaaaa"]
        out = capsys.readouterr().out
        assert "BLOCKED while polling" in out
        assert "2 channel(s) not polled" in out

    def test_stops_polling_after_a_rate_limited_exception_too(self, tmp_path, monkeypatch, capsys):
        manifest = tmp_path / "channels.txt"
        manifest.write_text("@alpha\n@beta\n", encoding="utf-8")

        def raise_on_alpha(url, cutoff, stop_after_old=5, probe_pace=1.0, limit_per_tab=None,
                           on_tab_error=None, on_rss_miss=None, on_cap_hit=None,
                           on_probe_error=None, **kwargs):
            if url.endswith("alpha"):
                raise sweep_module.yt.RateLimited("HTTP Error 429: Too Many Requests")
            raise AssertionError(f"must not poll {url} once alpha was blocked")

        monkeypatch.setattr(sweep_module.cv, "window_channel", raise_on_alpha)
        result = sweep(manifest, tmp_path / "out", since_days=3650, dry_run=True)

        assert result["channels_blocked"] == 1
        out = capsys.readouterr().out
        assert "BLOCKED while polling" in out
        assert "1 channel(s) not polled" in out

    def test_fallback_slug_is_noted_in_the_ok_log_line(self, tmp_path, monkeypatch, capsys):
        self.stub_channel(monkeypatch, [{"video_id": "aaaaaaaaaaa", "title": "one", "tab": "videos"}])
        monkeypatch.setattr(sweep_module, "ingest",
                            lambda v, o, hints=None, ingested=None:
                                {"slug": v, "skipped": False, "path": "p", "fallback_slug": True})

        sweep(self.manifest(tmp_path), tmp_path / "out", since_days=3650, pace=0)
        assert "fallback slug" in capsys.readouterr().out.lower()


def pytest_fail_ingest():
    raise AssertionError("ingest must not be called")


class TestExitCode:
    def result(self, **overrides):
        base = {"ingested": 0, "skipped": 0, "failed": 0, "blocked": False,
                "candidates": [], "channels_failed": 0, "channels_blocked": 0}
        return {**base, **overrides}

    def test_blocked_beats_everything(self):
        assert sweep_module.exit_code(self.result(blocked=True, failed=0, ingested=5)) == 2

    def test_failed_with_nothing_ingested_is_1(self):
        assert sweep_module.exit_code(self.result(failed=1)) == 1

    def test_channels_failed_with_nothing_ingested_is_1(self):
        assert sweep_module.exit_code(self.result(channels_failed=1)) == 1

    def test_a_failure_alongside_a_successful_ingest_is_0(self):
        assert sweep_module.exit_code(self.result(failed=1, ingested=1)) == 0

    def test_clean_sweep_is_0(self):
        assert sweep_module.exit_code(self.result()) == 0

    def test_channels_blocked_with_nothing_ingested_is_2(self):
        assert sweep_module.exit_code(self.result(channels_blocked=1)) == 2

    def test_channels_blocked_alongside_a_successful_ingest_is_still_2(self):
        """A blocked IP means channels are still un-walked no matter how many
        videos got ingested before the walk hit the block."""
        assert sweep_module.exit_code(self.result(channels_blocked=1, ingested=1)) == 2


class RequestBlocked(Exception):
    """Named like youtube_transcript_api's exception so its repr matches."""


def test_a_blocked_api_on_an_uncaptioned_video_fails_it_instead_of_backing_off(tmp_path, monkeypatch):
    """Through the real ingest and fetch_segments: the API is blocked, and
    yt-dlp answers definitively that there is nothing to fetch. That video is
    a failure to move past, not a rate limit to wait out for hours."""
    monkeypatch.setattr(
        sweep_module.cv, "window_channel",
        lambda url, cutoff, *a, **k: [{"video_id": "aaaaaaaaaaa", "title": "A Talk",
                                       "channel": "Chan", "published": "2026-09-01", "tab": "videos"}],
    )
    monkeypatch.delenv("YT_NO_API", raising=False)
    monkeypatch.setattr(sweep_module.yt, "via_api",
                        lambda v, l=None: (_ for _ in ()).throw(RequestBlocked()))
    monkeypatch.setattr(sweep_module.yt, "via_ytdlp",
                        lambda v: (_ for _ in ()).throw(RuntimeError("no vtt written")))
    sleeps = []
    monkeypatch.setattr(sweep_module.time, "sleep", sleeps.append)

    manifest = tmp_path / "channels.txt"
    manifest.write_text("@alpha\n", encoding="utf-8")
    result = sweep(manifest, tmp_path / "out", since_days=3650, pace=0)

    assert (result["failed"], result["blocked"]) == (1, False)
    assert all(s < 60 for s in sleeps)
    assert sweep_module.exit_code(result) == 1
