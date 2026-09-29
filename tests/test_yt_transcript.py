import pytest

import subprocess

import yt_transcript
from yt_transcript import (
    NotYetAired,
    RateLimited,
    extract_video_id,
    failure_kind,
    fetch_segments,
    format_timestamp,
    load_last_video,
    parse_vtt,
    render,
    run_ytdlp,
    save_last_video,
    vtt_time_to_seconds,
    ytdlp_failure,
)

VID = "Vitf8YaVXhc"


class TestExtractVideoId:
    @pytest.mark.parametrize("source", [
        VID,
        f"https://www.youtube.com/watch?v={VID}",
        f"https://youtu.be/{VID}",
        f"https://youtu.be/{VID}?si=abc123",
        f"https://www.youtu.be/{VID}",
        f"https://www.youtube.com/shorts/{VID}",
        f"https://www.youtube.com/embed/{VID}",
        f"https://www.youtube.com/live/{VID}",
        f"https://m.youtube.com/watch?v={VID}&feature=share",
        f"https://www.youtube.com/watch?list=PLxxx&v={VID}",
    ])
    def test_resolves(self, source):
        assert extract_video_id(source) == VID

    def test_strips_surrounding_whitespace(self):
        assert extract_video_id(f"  {VID}  ") == VID


class TestFormatTimestamp:
    def test_seconds_only(self):
        assert format_timestamp(45.0) == "[00:45]"

    def test_minutes(self):
        assert format_timestamp(125.0) == "[02:05]"

    def test_hours(self):
        assert format_timestamp(3725.0) == "[01:02:05]"

    def test_zero_and_none_are_safe(self):
        assert format_timestamp(0) == "[00:00]"
        assert format_timestamp(None) == "[00:00]"


class TestVttParsing:
    def test_time_to_seconds(self):
        assert vtt_time_to_seconds("00:01:23.456") == pytest.approx(83.456)
        assert vtt_time_to_seconds("01:23.500") == pytest.approx(83.5)

    def test_parses_cues_to_seconds_and_text(self):
        vtt = (
            "WEBVTT\nKind: captions\nLanguage: en\n\n"
            "00:00:01.000 --> 00:00:03.000\nhello world\n\n"
            "00:01:23.000 --> 00:01:25.000\nsecond cue\n"
        )
        assert parse_vtt(vtt) == [(1.0, "hello world"), (83.0, "second cue")]

    def test_drops_rolling_caption_repeats(self):
        """Auto-generated captions repeat the previous line as they scroll; each
        distinct line should survive exactly once."""
        vtt = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:03.000\nfirst line\n\n"
            "00:00:03.000 --> 00:00:05.000\nfirst line\nsecond line\n\n"
            "00:00:05.000 --> 00:00:07.000\nsecond line\nthird line\n"
        )
        assert [text for _, text in parse_vtt(vtt)] == ["first line", "second line", "third line"]

    def test_keeps_genuine_repeats_far_apart(self):
        """Regression: a transcript-wide dedup set deletes a speaker's every
        later "Right." and every "[Music]" marker after the first."""
        cues = [
            (1, "Right."), (10, "So the next point is this."),
            (300, "Right."), (540, "[Music]"), (720, "[Music]"),
        ]
        vtt = "WEBVTT\n\n" + "\n\n".join(
            f"00:{s // 60:02d}:{s % 60:02d}.000 --> 00:{s // 60:02d}:{s % 60 + 1:02d}.000\n{t}"
            for s, t in cues
        )
        assert [text for _, text in parse_vtt(vtt)] == [
            "Right.", "So the next point is this.", "Right.", "[Music]", "[Music]",
        ]

    def test_strips_karaoke_tags_and_unescapes_entities(self):
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n<c.colorE5E5E5>Tom &amp; Jerry</c>\n"
        assert parse_vtt(vtt) == [(1.0, "Tom & Jerry")]

    def test_empty_vtt_raises(self):
        with pytest.raises(RuntimeError):
            parse_vtt("WEBVTT\n\n")

    def test_ignores_style_block_before_first_cue(self):
        """A STYLE block's selector and declaration lines aren't timed text —
        without a cue they'd otherwise be emitted as a bogus 0.0 line."""
        vtt = (
            "WEBVTT\n\n"
            "STYLE\n::cue(c.colorE5E5E5) {\n  color: #E5E5E5;\n}\n\n"
            "00:00:01.000 --> 00:00:03.000\nhello\n"
        )
        assert parse_vtt(vtt) == [(1.0, "hello")]


class TestPreferredVtt:
    def test_plain_en_wins_over_a_regional_variant(self):
        """Sorting by name alone would pick en-GB, since '-' sorts before '.'."""
        files = ["vid.en-GB.vtt", "vid.en.vtt", "vid.en-orig.vtt"]
        assert yt_transcript._preferred_vtt(files) == "vid.en.vtt"

    def test_falls_back_to_whatever_exists(self):
        assert yt_transcript._preferred_vtt(["vid.en-GB.vtt"]) == "vid.en-GB.vtt"

    def test_none_when_no_captions(self):
        assert yt_transcript._preferred_vtt(["vid.mp4", "vid.json"]) is None


class TestImpersonateTarget:
    def test_chrome_when_curl_cffi_available(self, monkeypatch):
        monkeypatch.setattr(yt_transcript, "_IMPERSONATE_TARGET", None)
        out = "Client          OS           Source\n--------\nChrome-133      Macos-15     curl_cffi\n"
        monkeypatch.setattr(
            yt_transcript, "run_ytdlp",
            lambda args, timeout=None: subprocess.CompletedProcess(args, 0, stdout=out, stderr=""),
        )
        assert yt_transcript._impersonate_target() == "chrome"

    def test_empty_when_curl_cffi_unavailable(self, monkeypatch):
        """yt-dlp still lists Chrome targets when curl_cffi is missing, just
        marked unusable — passing --impersonate chrome then fails every call."""
        monkeypatch.setattr(yt_transcript, "_IMPERSONATE_TARGET", None)
        out = "Client          OS           Source\n--------\nChrome-133      Macos-15     curl_cffi (unavailable)\n"
        monkeypatch.setattr(
            yt_transcript, "run_ytdlp",
            lambda args, timeout=None: subprocess.CompletedProcess(args, 0, stdout=out, stderr=""),
        )
        assert yt_transcript._impersonate_target() == ""

    def test_empty_on_error(self, monkeypatch):
        monkeypatch.setattr(yt_transcript, "_IMPERSONATE_TARGET", None)

        def boom(args, timeout=None):
            raise subprocess.TimeoutExpired(cmd="yt-dlp", timeout=30)

        monkeypatch.setattr(yt_transcript, "run_ytdlp", boom)
        assert yt_transcript._impersonate_target() == ""

    def test_cached_after_first_call(self, monkeypatch):
        monkeypatch.setattr(yt_transcript, "_IMPERSONATE_TARGET", None)
        calls = []

        def fake_run(args, timeout=None):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, stdout="Chrome-133  Macos-15  curl_cffi\n", stderr="")

        monkeypatch.setattr(yt_transcript, "run_ytdlp", fake_run)
        yt_transcript._impersonate_target()
        yt_transcript._impersonate_target()
        assert len(calls) == 1


class TestViaYtdlp:
    def test_temp_directory_is_cleaned_up(self, monkeypatch, tmp_path):
        """A bulk sweep calls this hundreds of times; a leaked dir per fetch adds up."""
        created = {}

        def fake_mkdtemp():
            d = tmp_path / "work"
            d.mkdir()
            created["path"] = d
            (d / "vid.en.vtt").write_text(
                "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nhello\n", encoding="utf-8"
            )
            return str(d)

        monkeypatch.setattr(yt_transcript.tempfile, "mkdtemp", fake_mkdtemp)
        monkeypatch.setattr(yt_transcript, "_impersonate_target", lambda: "")
        monkeypatch.setattr(
            yt_transcript, "run_ytdlp",
            lambda args, timeout=None: subprocess.CompletedProcess(args, 0, stdout="", stderr=""),
        )

        assert yt_transcript.via_ytdlp("vid") == [(1.0, "hello")]
        assert not created["path"].exists()

    def test_temp_directory_is_cleaned_up_on_failure(self, monkeypatch, tmp_path):
        created = {}

        def fake_mkdtemp():
            d = tmp_path / "work_fail"
            d.mkdir()
            created["path"] = d
            return str(d)

        monkeypatch.setattr(yt_transcript.tempfile, "mkdtemp", fake_mkdtemp)
        monkeypatch.setattr(yt_transcript, "_impersonate_target", lambda: "")
        monkeypatch.setattr(
            yt_transcript, "run_ytdlp",
            lambda args, timeout=None: subprocess.CompletedProcess(args, 0, stdout="", stderr=""),
        )

        with pytest.raises(RuntimeError, match="no vtt"):
            yt_transcript.via_ytdlp("vid")
        assert not created["path"].exists()

    def test_uses_a_vtt_written_despite_a_nonzero_exit(self, monkeypatch, tmp_path):
        """--sub-lang en.* asks for several tracks; yt-dlp exits 1 when one
        track fails even though a wanted <id>.en.vtt is already on disk."""
        def fake_mkdtemp():
            d = tmp_path / "work"
            d.mkdir()
            (d / "vid.en.vtt").write_text(
                "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nhello\n", encoding="utf-8"
            )
            return str(d)

        monkeypatch.setattr(yt_transcript.tempfile, "mkdtemp", fake_mkdtemp)
        monkeypatch.setattr(yt_transcript, "_impersonate_target", lambda: "")
        monkeypatch.setattr(
            yt_transcript, "run_ytdlp",
            lambda args, timeout=None: subprocess.CompletedProcess(
                args, 1, stdout="", stderr="ERROR: Unable to download video subtitles for 'en-af'\n"
            ),
        )

        assert yt_transcript.via_ytdlp("vid") == [(1.0, "hello")]

    def test_raises_classified_error_on_nonzero_exit_with_no_vtt(self, monkeypatch, tmp_path):
        def fake_mkdtemp():
            d = tmp_path / "work"
            d.mkdir()
            return str(d)

        monkeypatch.setattr(yt_transcript.tempfile, "mkdtemp", fake_mkdtemp)
        monkeypatch.setattr(yt_transcript, "_impersonate_target", lambda: "")
        monkeypatch.setattr(
            yt_transcript, "run_ytdlp",
            lambda args, timeout=None: subprocess.CompletedProcess(
                args, 1, stdout="", stderr="ERROR: HTTP Error 429: Too Many Requests\n"
            ),
        )

        with pytest.raises(yt_transcript.RateLimited):
            yt_transcript.via_ytdlp("vid")

    def test_prefers_a_manual_caption_over_an_auto_one_with_the_plain_lang_code(self, monkeypatch, tmp_path):
        """A manual en-US track and an auto (ASR) 'en' track can both land on
        disk; filenames alone don't say which is manual, so the info.json's
        "subtitles" keys (the manual tracks) settle it."""
        def fake_mkdtemp():
            d = tmp_path / "work"
            d.mkdir()
            (d / "vid.en.vtt").write_text(
                "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nauto\n", encoding="utf-8"
            )
            (d / "vid.en-US.vtt").write_text(
                "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nmanual\n", encoding="utf-8"
            )
            (d / "vid.info.json").write_text(
                '{"subtitles": {"en-US": []}}', encoding="utf-8"
            )
            return str(d)

        monkeypatch.setattr(yt_transcript.tempfile, "mkdtemp", fake_mkdtemp)
        monkeypatch.setattr(yt_transcript, "_impersonate_target", lambda: "")
        monkeypatch.setattr(
            yt_transcript, "run_ytdlp",
            lambda args, timeout=None: subprocess.CompletedProcess(args, 0, stdout="", stderr=""),
        )

        assert yt_transcript.via_ytdlp("vid") == [(1.0, "manual")]

    def test_falls_back_to_old_ranking_when_info_json_missing(self, monkeypatch, tmp_path):
        def fake_mkdtemp():
            d = tmp_path / "work"
            d.mkdir()
            (d / "vid.en.vtt").write_text(
                "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nplain\n", encoding="utf-8"
            )
            (d / "vid.en-GB.vtt").write_text(
                "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nregional\n", encoding="utf-8"
            )
            return str(d)

        monkeypatch.setattr(yt_transcript.tempfile, "mkdtemp", fake_mkdtemp)
        monkeypatch.setattr(yt_transcript, "_impersonate_target", lambda: "")
        monkeypatch.setattr(
            yt_transcript, "run_ytdlp",
            lambda args, timeout=None: subprocess.CompletedProcess(args, 0, stdout="", stderr=""),
        )

        assert yt_transcript.via_ytdlp("vid") == [(1.0, "plain")]

class TestRender:
    segments = [(0.0, "alpha"), (65.0, "beta")]

    def test_with_timestamps(self):
        assert render(self.segments, True) == "[00:00] alpha\n[01:05] beta"

    def test_without_timestamps(self):
        assert render(self.segments, False) == "alpha beta"


class TestFetchSegments:
    def test_prefers_the_api(self, monkeypatch):
        monkeypatch.delenv("YT_NO_API", raising=False)
        monkeypatch.setattr(yt_transcript, "via_api", lambda v, l=None: [(0.0, "api")])
        monkeypatch.setattr(yt_transcript, "via_ytdlp", lambda v: [(0.0, "ytdlp")])
        assert fetch_segments(VID) == ([(0.0, "api")], "api")

    def test_falls_back_to_ytdlp_when_the_api_fails(self, monkeypatch):
        monkeypatch.delenv("YT_NO_API", raising=False)

        def boom(video_id, languages=None):
            raise RuntimeError("api blocked")

        monkeypatch.setattr(yt_transcript, "via_api", boom)
        monkeypatch.setattr(yt_transcript, "via_ytdlp", lambda v: [(0.0, "ytdlp")])
        assert fetch_segments(VID) == ([(0.0, "ytdlp")], "ytdlp")

    def test_yt_no_api_skips_the_api_entirely(self, monkeypatch):
        monkeypatch.setenv("YT_NO_API", "1")

        def must_not_run(video_id, languages=None):
            raise AssertionError("the API should not be called when YT_NO_API is set")

        monkeypatch.setattr(yt_transcript, "via_api", must_not_run)
        monkeypatch.setattr(yt_transcript, "via_ytdlp", lambda v: [(0.0, "ytdlp")])
        assert fetch_segments(VID) == ([(0.0, "ytdlp")], "ytdlp")

    def test_reports_both_failures_when_neither_source_works(self, monkeypatch):
        monkeypatch.delenv("YT_NO_API", raising=False)

        def api_boom(video_id, languages=None):
            raise RuntimeError("api blocked")

        def ytdlp_boom(video_id):
            raise RuntimeError("no vtt written")

        monkeypatch.setattr(yt_transcript, "via_api", api_boom)
        monkeypatch.setattr(yt_transcript, "via_ytdlp", ytdlp_boom)
        with pytest.raises(RuntimeError, match="api blocked.*no vtt written"):
            fetch_segments(VID)

    def test_raises_rate_limited_when_ytdlp_was_rate_limited(self, monkeypatch):
        monkeypatch.delenv("YT_NO_API", raising=False)
        monkeypatch.setattr(yt_transcript, "via_api", lambda v, l=None: (_ for _ in ()).throw(RuntimeError("api blocked")))
        monkeypatch.setattr(
            yt_transcript, "via_ytdlp",
            lambda v: (_ for _ in ()).throw(RateLimited("HTTP Error 429")),
        )
        with pytest.raises(RateLimited):
            fetch_segments(VID)

    def test_raises_not_yet_aired_when_ytdlp_saw_a_premiere(self, monkeypatch):
        monkeypatch.delenv("YT_NO_API", raising=False)
        monkeypatch.setattr(yt_transcript, "via_api", lambda v, l=None: (_ for _ in ()).throw(RuntimeError("api blocked")))
        monkeypatch.setattr(
            yt_transcript, "via_ytdlp",
            lambda v: (_ for _ in ()).throw(NotYetAired("premiere")),
        )
        with pytest.raises(NotYetAired):
            fetch_segments(VID)

    def test_rate_limited_wins_when_either_source_saw_it(self, monkeypatch):
        """The api failure alone can carry the rate-limit signal even when
        yt-dlp's failure is unrelated."""
        monkeypatch.delenv("YT_NO_API", raising=False)
        monkeypatch.setattr(
            yt_transcript, "via_api",
            lambda v, l=None: (_ for _ in ()).throw(RuntimeError("429 Client Error")),
        )
        monkeypatch.setattr(
            yt_transcript, "via_ytdlp",
            lambda v: (_ for _ in ()).throw(RuntimeError("no vtt written")),
        )
        with pytest.raises(RateLimited):
            fetch_segments(VID)


class TestFailureKind:
    def test_rate_limited_by_type(self):
        assert failure_kind(RateLimited("blocked")) == "rate_limited"

    def test_not_yet_aired_by_type(self):
        assert failure_kind(NotYetAired("early")) == "not_yet_aired"

    @pytest.mark.parametrize("message", [
        "IpBlocked",
        "RequestBlocked",
        "HTTP Error 429: Too Many Requests",
        "429 Client Error: Too Many Requests",
        "Sign in to confirm you're not a bot",
        "Sign in to confirm you’re not a bot",
    ])
    def test_rate_limited_by_message(self, message):
        assert failure_kind(message) == "rate_limited"

    def test_ids_containing_429_are_not_rate_limited(self):
        """11-char video ids are [A-Za-z0-9_-]; one that happens to contain
        "429" must not be misread as a rate-limit signal."""
        assert failure_kind("transcript unavailable for ab429cdefgh: boom") == "failed"

    @pytest.mark.parametrize("message", [
        "premiere",
        "This live event will begin in 3 hours",
        "Video is not yet available",
        "The uploader hasn't started streaming",
    ])
    def test_not_yet_aired_by_message(self, message):
        assert failure_kind(message) == "not_yet_aired"

    @pytest.mark.parametrize("message", [
        "Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo failed>",
        "urlopen error timed out",
        "getaddrinfo failed",
        "Temporary failure in name resolution",
        "Connection reset by peer",
        "Connection refused",
        "Connection aborted",
        "Remote end closed connection without response",
        "CERTIFICATE_VERIFY_FAILED",
        "HTTP Error 503: Service Unavailable",
        "Unable to download webpage: <urlopen error [WinError 10060] A connection attempt "
        "failed because the connected party did not properly respond after a period of time>",
        "Unable to download webpage: [WinError 10061] No connection could be made because "
        "the target machine actively refused it",
        "Unable to download API page: [Errno 101] Network is unreachable",
        "Unable to download webpage: EOF occurred in violation of protocol (_ssl.c:2427)",
        "Unable to download webpage: IncompleteRead(1024 bytes read, 2048 more expected)",
    ])
    def test_network_by_message(self, message):
        assert failure_kind(message) == "network"

    def test_anything_else_is_failed(self):
        assert failure_kind("captions disabled for this video") == "failed"

    @pytest.mark.parametrize("message", [
        "Unable to download API page: HTTP Error 404: Not Found",
        "Unable to download webpage: HTTP Error 403: Forbidden",
    ])
    def test_permanent_download_failure_is_not_network(self, message):
        """yt-dlp prefixes permanent 4xx errors with "Unable to download",
        the same phrasing it uses for a genuine connectivity failure — one
        broken video must not abort its whole channel's probe walk."""
        assert failure_kind(message) == "failed"

    def test_rate_limited_takes_precedence_over_not_yet_aired(self):
        assert failure_kind("429 Client Error and also a premiere") == "rate_limited"


class TestRunYtdlp:
    def test_builds_command_with_utf8_encoding(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(yt_transcript.subprocess, "run", fake_run)
        run_ytdlp(["--skip-download", "VIDEOID"])

        assert captured["cmd"] == ["yt-dlp", "--encoding", "utf-8", "--skip-download", "VIDEOID"]
        assert captured["kwargs"]["capture_output"] is True
        assert captured["kwargs"]["text"] is True
        assert captured["kwargs"]["encoding"] == "utf-8"
        assert captured["kwargs"]["errors"] == "replace"
        assert "check" not in captured["kwargs"]

    def test_passes_through_timeout(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(yt_transcript.subprocess, "run", fake_run)
        run_ytdlp(["--version"], timeout=30)

        assert captured["kwargs"]["timeout"] == 30


class TestYtdlpFailure:
    def test_message_is_last_nonempty_stderr_line(self):
        result = subprocess.CompletedProcess(
            ["yt-dlp"], 1, stdout="", stderr="warning: foo\nERROR: bar\n\n"
        )
        exc = ytdlp_failure(result)
        assert str(exc) == "ERROR: bar"

    def test_falls_back_to_exit_code_when_stderr_empty(self):
        result = subprocess.CompletedProcess(["yt-dlp"], 2, stdout="", stderr="")
        exc = ytdlp_failure(result)
        assert str(exc) == "yt-dlp exited 2"

    def test_classifies_as_rate_limited(self):
        result = subprocess.CompletedProcess(
            ["yt-dlp"], 1, stdout="", stderr="ERROR: HTTP Error 429: Too Many Requests\n"
        )
        assert isinstance(ytdlp_failure(result), RateLimited)

    def test_classifies_as_not_yet_aired(self):
        result = subprocess.CompletedProcess(
            ["yt-dlp"], 1, stdout="", stderr="ERROR: This live event will begin in 2 hours\n"
        )
        assert isinstance(ytdlp_failure(result), NotYetAired)

    def test_classifies_as_plain_runtime_error(self):
        result = subprocess.CompletedProcess(
            ["yt-dlp"], 1, stdout="", stderr="ERROR: Video unavailable\n"
        )
        exc = ytdlp_failure(result)
        assert type(exc) is RuntimeError


class TestLastVideoState:
    def test_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr(yt_transcript, "STATE_FILE", tmp_path / ".last_video")
        save_last_video(VID)
        assert load_last_video() == VID

    def test_returns_none_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(yt_transcript, "STATE_FILE", tmp_path / ".last_video")
        assert load_last_video() is None
