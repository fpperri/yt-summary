from types import SimpleNamespace

import pytest

import ingest_video
from ingest_video import (
    build_document,
    fetch_metadata,
    ingest,
    ingested_videos,
    resolve_target,
    transcript_video_id,
)

VID = "Vitf8YaVXhc"
OTHER = "dQw4w9WgXcQ"


def write_source_set(root, slug, video_id):
    folder = root / slug
    folder.mkdir(parents=True)
    (folder / f"transcript.{slug}.md").write_text(
        f"---\nvideo_id: {video_id}\n---\n# t\n", encoding="utf-8"
    )
    return folder


class TestTranscriptVideoId:
    def test_reads_the_id_from_frontmatter(self, tmp_path):
        folder = write_source_set(tmp_path, "chan-title", VID)
        assert transcript_video_id(folder / "transcript.chan-title.md") == VID

    def test_missing_file_is_none(self, tmp_path):
        assert transcript_video_id(tmp_path / "nope.md") is None


class TestIngestedVideos:
    def test_maps_id_to_its_folder(self, tmp_path):
        folder = write_source_set(tmp_path, "a-one", VID)
        assert ingested_videos(tmp_path) == {VID: folder}

    def test_collects_across_folders(self, tmp_path):
        write_source_set(tmp_path, "a-one", VID)
        write_source_set(tmp_path, "b-two", OTHER)
        assert set(ingested_videos(tmp_path)) == {VID, OTHER}

    def test_empty_directory(self, tmp_path):
        assert ingested_videos(tmp_path) == {}


class TestResolveTarget:
    def test_fresh_video_is_not_already_ingested(self, tmp_path):
        target, already = resolve_target(tmp_path, "chan-title", VID)
        assert target == tmp_path / "chan-title"
        assert already is False

    def test_same_video_again_is_recognised(self, tmp_path):
        write_source_set(tmp_path, "chan-title", VID)
        target, already = resolve_target(tmp_path, "chan-title", VID)
        assert target == tmp_path / "chan-title"
        assert already is True

    def test_colliding_slug_gets_its_own_folder(self, tmp_path):
        """A re-upload slugs identically to the original. It must not overwrite
        the existing transcript."""
        write_source_set(tmp_path, "chan-title", VID)
        target, already = resolve_target(tmp_path, "chan-title", OTHER)
        assert target == tmp_path / f"chan-title-{OTHER}"
        assert already is False

    def test_already_disambiguated_collision_is_recognised(self, tmp_path):
        write_source_set(tmp_path, "chan-title", VID)
        write_source_set(tmp_path, f"chan-title-{OTHER}", OTHER)
        _, already = resolve_target(tmp_path, "chan-title", OTHER)
        assert already is True


class TestBuildDocument:
    def test_quotes_titles_containing_yaml_metacharacters(self):
        doc = build_document(
            VID, 'Rust: the "why" # explained', "Some Channel",
            "2026-01-02", "2026-09-01", "api", [(0.0, "hi")],
        )
        assert 'title: "Rust: the \\"why\\" # explained"' in doc

    def test_includes_metadata_and_timestamped_body(self):
        doc = build_document(
            VID, "Title", "Chan", "2026-01-02", "2026-09-01", "api",
            [(0.0, "first"), (65.0, "second")],
        )
        assert f'video_id: "{VID}"' in doc
        assert "fetched_via: api" in doc
        assert "published: 2026-01-02" in doc
        assert "[00:00] first" in doc
        assert "[01:05] second" in doc

    def test_falls_back_to_the_id_as_heading(self):
        doc = build_document(VID, "", "", "", "2026-09-01", "api", [(0.0, "x")])
        assert f"# {VID}" in doc

    def test_contains_no_cortex_paths(self):
        doc = build_document(VID, "T", "C", "", "2026-09-01", "api", [(0.0, "x")])
        assert ".cortex" not in doc
        assert "inquiry/sources" not in doc

    def test_numeric_looking_video_id_survives_yaml_parsing_as_a_string(self):
        yaml = pytest.importorskip("yaml")
        for numeric_looking_id in ("12345678901", "1e123456789"):
            doc = build_document(
                numeric_looking_id, "T", "C", "", "2026-09-01", "api", [(0.0, "x")],
            )
            frontmatter = doc.split("---")[1]
            parsed = yaml.safe_load(frontmatter)
            assert parsed["video_id"] == numeric_looking_id

    def test_quoted_video_id_round_trips_through_transcript_video_id(self, tmp_path):
        doc = build_document(VID, "T", "C", "", "2026-09-01", "api", [(0.0, "x")])
        path = tmp_path / "doc.md"
        path.write_text(doc, encoding="utf-8")
        assert transcript_video_id(path) == VID


class TestFetchMetadata:
    def _ytdlp(self, monkeypatch, stdout="", returncode=0):
        monkeypatch.setattr(
            ingest_video.yt, "run_ytdlp",
            lambda args, timeout=None: SimpleNamespace(returncode=returncode, stdout=stdout, stderr=""),
        )

    def _oembed(self, monkeypatch, title="", channel=""):
        monkeypatch.setattr(ingest_video, "fetch_metadata_oembed", lambda vid: (title, channel))

    def _oembed_raises(self, monkeypatch):
        def raiser(vid):
            raise RuntimeError("oembed unavailable")
        monkeypatch.setattr(ingest_video, "fetch_metadata_oembed", raiser)

    def test_reads_title_channel_and_date_from_ytdlp(self, monkeypatch):
        self._ytdlp(monkeypatch, stdout="A Talk\tSome Channel\t20260102\n")
        self._oembed_raises(monkeypatch)
        assert fetch_metadata(VID) == ("A Talk", "Some Channel", "20260102")

    def test_falls_back_to_oembed_when_ytdlp_fails_entirely(self, monkeypatch):
        self._ytdlp(monkeypatch, returncode=1)
        self._oembed(monkeypatch, title="Oembed Title", channel="Oembed Channel")
        assert fetch_metadata(VID) == ("Oembed Title", "Oembed Channel", "")

    def test_oembed_fills_only_the_field_ytdlp_left_empty(self, monkeypatch):
        self._ytdlp(monkeypatch, stdout="A Talk\tNA\t20260102\n")
        self._oembed(monkeypatch, title="Oembed Title", channel="Oembed Channel")
        assert fetch_metadata(VID) == ("A Talk", "Oembed Channel", "20260102")

    def test_both_sources_failing_yields_empty_strings_without_raising(self, monkeypatch):
        self._ytdlp(monkeypatch, returncode=1)
        self._oembed_raises(monkeypatch)
        assert fetch_metadata(VID) == ("", "", "")

    def test_tolerates_formats_yt_dlp_cannot_select(self, monkeypatch):
        """--skip-download still runs format selection; a withheld-formats
        video must not blank the title and channel it already resolved."""
        captured = {}

        def fake_run(args, timeout=None):
            captured["args"] = args
            return SimpleNamespace(returncode=0, stdout="A Talk\tSome Channel\t20260102\n", stderr="")

        monkeypatch.setattr(ingest_video.yt, "run_ytdlp", fake_run)
        self._oembed_raises(monkeypatch)
        fetch_metadata(VID)
        assert "--ignore-no-formats-error" in captured["args"]


class TestIngest:
    def _stub(self, monkeypatch, segments=None):
        monkeypatch.setattr(ingest_video, "fetch_metadata",
                            lambda vid: ("A Talk", "Some Channel", "20260102"))
        monkeypatch.setattr(ingest_video.yt, "fetch_segments",
                            lambda vid: (segments or [(0.0, "hello")], "api"))

    def test_writes_a_source_set(self, tmp_path, monkeypatch):
        self._stub(monkeypatch)
        result = ingest(f"https://www.youtube.com/watch?v={VID}", tmp_path)

        assert result["slug"] == "some-channel-a-talk"
        assert result["skipped"] is False
        assert result["fallback_slug"] is False
        written = tmp_path / "some-channel-a-talk" / "transcript.some-channel-a-talk.md"
        assert written.exists()
        assert "[00:00] hello" in written.read_text(encoding="utf-8")

    def test_second_run_skips_without_refetching(self, tmp_path, monkeypatch):
        self._stub(monkeypatch)
        ingest(VID, tmp_path)

        def must_not_run(vid):
            raise AssertionError("an already-ingested video must not be refetched")

        monkeypatch.setattr(ingest_video.yt, "fetch_segments", must_not_run)
        assert ingest(VID, tmp_path)["skipped"] is True

    def test_second_run_skips_before_fetching_metadata(self, tmp_path, monkeypatch):
        self._stub(monkeypatch)
        ingest(VID, tmp_path)

        def must_not_run(vid):
            raise AssertionError("an already-ingested video must not have its metadata refetched")

        monkeypatch.setattr(ingest_video, "fetch_metadata", must_not_run)
        assert ingest(VID, tmp_path)["skipped"] is True

    def test_precomputed_ingested_map_is_used_instead_of_scanning(self, tmp_path, monkeypatch):
        self._stub(monkeypatch)
        first = ingest(VID, tmp_path)

        def must_not_run(vid):
            raise AssertionError("a precomputed ingested map must short-circuit the fetch")

        monkeypatch.setattr(ingest_video, "fetch_metadata", must_not_run)
        monkeypatch.setattr(ingest_video.yt, "fetch_segments", must_not_run)
        precomputed = {VID: tmp_path / first["slug"]}
        assert ingest(VID, tmp_path, ingested=precomputed)["skipped"] is True

    def test_force_refetches(self, tmp_path, monkeypatch):
        self._stub(monkeypatch)
        ingest(VID, tmp_path)
        self._stub(monkeypatch, segments=[(1.0, "refetched")])
        result = ingest(VID, tmp_path, force=True)

        assert result["skipped"] is False
        written = tmp_path / result["slug"] / f"transcript.{result['slug']}.md"
        assert "refetched" in written.read_text(encoding="utf-8")

    def test_force_rewrites_the_existing_folder_when_the_slug_would_change(self, tmp_path, monkeypatch):
        """A changed title reslugs the video; force must still land in the folder
        the frontmatter id already lives in rather than starting a second copy."""
        self._stub(monkeypatch)
        first = ingest(VID, tmp_path)

        monkeypatch.setattr(ingest_video, "fetch_metadata",
                            lambda vid: ("A Different Talk", "Some Channel", "20260102"))
        monkeypatch.setattr(ingest_video.yt, "fetch_segments",
                            lambda vid: ([(0.0, "renamed")], "api"))
        result = ingest(VID, tmp_path, force=True)

        assert result["path"] == first["path"]
        assert len(list(tmp_path.iterdir())) == 1
        written = tmp_path / result["slug"] / f"transcript.{result['slug']}.md"
        assert "renamed" in written.read_text(encoding="utf-8")

    def test_force_rewrites_the_transcript_in_a_folder_the_user_renamed(self, tmp_path, monkeypatch):
        """A user-renamed folder still holds transcript.<old-slug>.md; force
        must overwrite that file rather than adding a second transcript."""
        self._stub(monkeypatch)
        first = ingest(VID, tmp_path)
        old_folder = tmp_path / first["slug"]
        renamed_folder = tmp_path / "renamed-by-user"
        old_folder.rename(renamed_folder)

        monkeypatch.setattr(ingest_video, "fetch_metadata",
                            lambda vid: ("A Different Talk", "Some Channel", "20260102"))
        monkeypatch.setattr(ingest_video.yt, "fetch_segments",
                            lambda vid: ([(0.0, "renamed folder refetch")], "api"))
        result = ingest(VID, tmp_path, force=True, ingested={VID: renamed_folder})

        assert result["slug"] == first["slug"]
        files = list(renamed_folder.glob("*.md"))
        assert len(files) == 1
        assert "renamed folder refetch" in files[0].read_text(encoding="utf-8")

    def test_hints_are_used_and_metadata_is_not_fetched_when_complete(self, tmp_path, monkeypatch):
        def must_not_run(vid):
            raise AssertionError("hints covering title and channel make a metadata fetch pointless")

        monkeypatch.setattr(ingest_video, "fetch_metadata", must_not_run)
        monkeypatch.setattr(ingest_video.yt, "fetch_segments", lambda vid: ([(0.0, "hi")], "api"))

        hints = {"title": "Hinted Talk", "channel": "Hinted Channel", "published": "2026-03-04"}
        result = ingest(VID, tmp_path, hints=hints)

        assert result["fallback_slug"] is False
        written = tmp_path / result["slug"] / f"transcript.{result['slug']}.md"
        text = written.read_text(encoding="utf-8")
        assert 'title: "Hinted Talk"' in text
        assert 'channel: "Hinted Channel"' in text
        assert "published: 2026-03-04" in text

    def test_partial_hints_fill_only_the_missing_fields_from_metadata(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ingest_video, "fetch_metadata",
                            lambda vid: ("Metadata Title", "Metadata Channel", "20260102"))
        monkeypatch.setattr(ingest_video.yt, "fetch_segments", lambda vid: ([(0.0, "hi")], "api"))

        result = ingest(VID, tmp_path, hints={"title": "Hinted Talk"})

        written = tmp_path / result["slug"] / f"transcript.{result['slug']}.md"
        text = written.read_text(encoding="utf-8")
        assert 'title: "Hinted Talk"' in text
        assert 'channel: "Metadata Channel"' in text
        assert "published: 2026-01-02" in text

    def test_hinted_published_is_not_overwritten_by_metadata(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ingest_video, "fetch_metadata",
                            lambda vid: ("Metadata Title", "Metadata Channel", "20260102"))
        monkeypatch.setattr(ingest_video.yt, "fetch_segments", lambda vid: ([(0.0, "hi")], "api"))

        result = ingest(VID, tmp_path, hints={"published": "2020-01-01"})

        written = tmp_path / result["slug"] / f"transcript.{result['slug']}.md"
        assert "published: 2020-01-01" in written.read_text(encoding="utf-8")

    def test_force_keeps_prior_metadata_when_a_refetch_comes_back_empty(self, tmp_path, monkeypatch):
        """A metadata outage during --force must not blank a title and
        channel the previous ingest already resolved."""
        self._stub(monkeypatch)
        ingest(VID, tmp_path)

        monkeypatch.setattr(ingest_video, "fetch_metadata", lambda vid: ("", "", ""))
        monkeypatch.setattr(ingest_video.yt, "fetch_segments", lambda vid: ([(9.0, "refetched")], "api"))
        result = ingest(VID, tmp_path, force=True)

        assert result["fallback_slug"] is False
        written = tmp_path / result["slug"] / f"transcript.{result['slug']}.md"
        text = written.read_text(encoding="utf-8")
        assert 'title: "A Talk"' in text
        assert 'channel: "Some Channel"' in text
        assert "published: 2026-01-02" in text
        assert "refetched" in text

    def test_missing_metadata_still_yields_a_unique_slug(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ingest_video, "fetch_metadata", lambda vid: ("", "", ""))
        monkeypatch.setattr(ingest_video.yt, "fetch_segments", lambda vid: ([(0.0, "x")], "api"))

        first = ingest(VID, tmp_path)
        second = ingest(OTHER, tmp_path)
        assert first["slug"] != second["slug"]
        assert first["fallback_slug"] is True
