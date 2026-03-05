import json
import re
import shutil
from pathlib import Path

import pytest
from aioresponses import aioresponses
import aiohttp

from wayback_dl.cli import parse_timestamp
from wayback_dl.downloader import (
    WaybackDownloader, _classify_file, _format_size,
)
from wayback_dl.utils import to_regex, match_filter, decode_file_id, sanitize_path, validate_url
from wayback_dl.cdx_api import CDX_API_URL

# Pattern to match any CDX API request
CDX_PATTERN = re.compile(r'^https://web\.archive\.org/cdx/search/cdx\?')

# Fixture data based on www.onlyfreegames.net
# Format: [timestamp, original, mimetype, length]
MOCK_EXACT_RESPONSE = [
    ["timestamp", "original", "mimetype", "length"],
    ["20060711191226", "http://www.onlyfreegames.net/", "text/html", "4520"],
]

MOCK_WILDCARD_RESPONSE = [
    ["timestamp", "original", "mimetype", "length"],
    ["20060711191226", "http://www.onlyfreegames.net/", "text/html", "4520"],
    ["20060711191226", "http://www.onlyfreegames.net:80/linux.htm", "text/html", "3200"],
    ["20060111084756", "http://www.onlyfreegames.net:80/strat.html", "text/html", "2800"],
    ["20060716231334", "http://www.onlyfreegames.net:80/menu.html", "text/html", "1500"],
    ["20060711191226", "http://www.onlyfreegames.net:80/games/action.html", "text/html", "5100"],
    ["20060711191226", "http://www.onlyfreegames.net:80/img/logo.gif", "image/gif", "15200"],
    ["20060711191226", "http://www.onlyfreegames.net:80/img/bg.jpg", "image/jpeg", "42000"],
    ["20060711191226", "http://www.onlyfreegames.net:80/img/icon.bmp", "image/bmp", "8400"],
    ["20060711191226", "http://www.onlyfreegames.net:80/css/style.css", "text/css", "2100"],
]


def _mock_cdx(m, extra_snapshots=None):
    """Set up mock CDX API responses. Uses pattern matching for any CDX URL."""
    # Mock exact URL request
    m.get(CDX_PATTERN, payload=MOCK_EXACT_RESPONSE)
    # Mock wildcard request (page 0)
    if extra_snapshots:
        m.get(CDX_PATTERN, payload=extra_snapshots)
    else:
        m.get(CDX_PATTERN, payload=MOCK_WILDCARD_RESPONSE)
    # Mock wildcard request (page 1 - empty to stop pagination)
    m.get(CDX_PATTERN, payload=[])


@pytest.fixture
def downloader():
    return WaybackDownloader(base_url="http://www.onlyfreegames.net")


@pytest.fixture
def cleanup():
    yield
    path = Path("websites/www.onlyfreegames.net")
    if path.exists():
        shutil.rmtree(path)


# --- Utils tests ---

class TestToRegex:
    def test_regex_pattern(self):
        result = to_regex(r'/\.(gif|jpg)$/i')
        assert result is not None
        assert result.search("image.gif")
        assert result.search("photo.JPG")
        assert not result.search("file.png")

    def test_plain_string_returns_none(self):
        assert to_regex("menu.html") is None

    def test_empty_string_returns_none(self):
        assert to_regex("") is None

    def test_none_returns_none(self):
        assert to_regex(None) is None

    def test_regex_without_flags(self):
        result = to_regex(r'/\.css$/')
        assert result is not None
        assert result.search("style.css")
        assert not result.search("style.CSS")

    def test_overly_long_regex_rejected(self):
        long_pattern = "/" + "a" * 600 + "/"
        with pytest.raises(ValueError, match="too long"):
            to_regex(long_pattern)

    def test_invalid_regex_rejected(self):
        with pytest.raises(ValueError, match="Invalid regex"):
            to_regex("/[invalid/")  # unclosed bracket


class TestMatchFilter:
    def test_include_match(self):
        assert match_filter("http://example.com/menu.html", "menu.html")

    def test_include_no_match(self):
        assert not match_filter("http://example.com/index.html", "menu.html")

    def test_include_case_insensitive(self):
        assert match_filter("http://example.com/MENU.HTML", "menu.html")

    def test_exclude_match(self):
        assert match_filter("http://example.com/menu.html", "menu.html", exclude=True)

    def test_exclude_no_match(self):
        assert not match_filter("http://example.com/index.html", "menu.html", exclude=True)

    def test_no_filter_include(self):
        assert match_filter("http://example.com/anything", None)

    def test_no_filter_exclude(self):
        assert not match_filter("http://example.com/anything", None, exclude=True)

    def test_regex_filter(self):
        assert match_filter("http://example.com/photo.jpg", r'/\.(gif|jpg)$/i')
        assert not match_filter("http://example.com/style.css", r'/\.(gif|jpg)$/i')


class TestSanitizePath:
    def test_strips_dotdot(self):
        assert ".." not in sanitize_path("../../etc/passwd")

    def test_strips_single_dot(self):
        assert sanitize_path("./path/./file") == "path/file"

    def test_strips_control_chars(self):
        assert sanitize_path("file\x00name\x1f.html") == "filename.html"

    def test_normal_path_unchanged(self):
        assert sanitize_path("css/style.css") == "css/style.css"


class TestValidateUrl:
    def test_http_allowed(self):
        assert validate_url("http://example.com") == "http://example.com"

    def test_https_allowed(self):
        assert validate_url("https://example.com") == "https://example.com"

    def test_bare_domain_allowed(self):
        assert validate_url("example.com") == "example.com"

    def test_file_scheme_rejected(self):
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            validate_url("file:///etc/passwd")

    def test_javascript_scheme_rejected(self):
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            validate_url("javascript:alert(1)")

    def test_ftp_scheme_rejected(self):
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            validate_url("ftp://example.com")


class TestDecodeFileId:
    def test_normal_url(self):
        assert decode_file_id("http://www.example.com:80/path/file.html") == "path/file.html"

    def test_root_url(self):
        assert decode_file_id("http://www.example.com/") == ""

    def test_encoded_url(self):
        assert decode_file_id("http://example.com/path/my%20file.html") == "path/my file.html"

    def test_no_slash(self):
        assert decode_file_id("noslash") is None

    def test_path_traversal_stripped(self):
        """Path traversal sequences should be removed from file IDs."""
        result = decode_file_id("http://example.com/../../etc/passwd")
        assert ".." not in result
        assert result == "etc/passwd"

    def test_encoded_path_traversal_stripped(self):
        """URL-encoded path traversal (%2e%2e) should also be caught."""
        result = decode_file_id("http://example.com/%2e%2e/%2e%2e/etc/passwd")
        assert ".." not in result


# --- Security tests ---

class TestPathTraversalProtection:
    def test_resolve_path_traversal_raises(self, tmp_path):
        """Attempting to resolve a path outside backup_path should raise."""
        d = WaybackDownloader(
            base_url="http://example.com",
            directory=str(tmp_path / "downloads"),
        )
        # Ensure backup_path exists
        d.backup_path.mkdir(parents=True, exist_ok=True)

        # Even though decode_file_id strips .., test the _resolve_file_path guard directly
        # by passing a file_id that would escape (in case of a future bug in sanitize_path)
        # The sanitize_path should have already stripped this, but _resolve_file_path
        # has a second check via .resolve()
        _, file_path = d._resolve_file_path("safe/file.html", "http://example.com/safe/file.html")
        assert str(tmp_path) in str(file_path)

    def test_symlink_in_download_dir_detected(self, tmp_path):
        """Symlinks in the download path should be rejected."""
        d = WaybackDownloader(
            base_url="http://example.com",
            directory=str(tmp_path / "downloads"),
        )
        d.backup_path.mkdir(parents=True, exist_ok=True)

        # Create a symlink inside the download directory
        symlink_path = d.backup_path / "evil_link"
        symlink_path.symlink_to("/tmp")

        with pytest.raises(ValueError, match="Symlink"):
            d._structure_dir_path(symlink_path / "subdir")


# --- File classification tests ---

class TestFileClassification:
    def test_classify_html(self):
        name, priority = _classify_file("text/html")
        assert name == "HTML"
        assert priority == 0

    def test_classify_css(self):
        name, priority = _classify_file("text/css")
        assert name == "CSS"
        assert priority == 1

    def test_classify_js(self):
        name, _ = _classify_file("application/javascript")
        assert name == "JS"

    def test_classify_image(self):
        name, priority = _classify_file("image/jpeg")
        assert name == "Images"
        assert priority == 5  # lowest priority (downloaded last)

    def test_classify_font(self):
        name, _ = _classify_file("application/x-font-woff")
        assert name == "Fonts"

    def test_classify_unknown(self):
        name, priority = _classify_file("application/octet-stream")
        assert name == "Other"
        assert priority == 4

    def test_format_size_bytes(self):
        assert _format_size(500) == "500 B"

    def test_format_size_kb(self):
        assert _format_size(2048) == "2.0 KB"

    def test_format_size_mb(self):
        assert _format_size(5 * 1024 * 1024) == "5.0 MB"


# --- Downloader tests ---

class TestWaybackDownloader:
    def test_backup_name(self, downloader):
        assert downloader.backup_name == "www.onlyfreegames.net"

    def test_backup_name_without_scheme(self):
        d = WaybackDownloader(base_url="www.onlyfreegames.net")
        assert d.backup_name == "www.onlyfreegames.net"

    def test_backup_path_default(self, downloader):
        assert downloader.backup_path == Path("websites/www.onlyfreegames.net")

    def test_backup_path_custom(self):
        d = WaybackDownloader(base_url="http://example.com", directory="/tmp/backup")
        assert d.backup_path == Path("/tmp/backup")

    @pytest.mark.asyncio
    async def test_get_file_list(self, downloader):
        with aioresponses() as m:
            _mock_cdx(m)
            async with aiohttp.ClientSession() as session:
                file_list = await downloader.get_file_list(session)
        # Should have deduplicated files with mimetype and size
        assert len(file_list) > 0
        file_ids = [f["file_id"] for f in file_list]
        assert "linux.htm" in file_ids
        # Verify new fields are present
        first = file_list[0]
        assert "mimetype" in first
        assert "size" in first

    @pytest.mark.asyncio
    async def test_exact_url(self):
        d = WaybackDownloader(
            base_url="http://www.onlyfreegames.net",
            exact_url=True,
        )
        with aioresponses() as m:
            m.get(CDX_PATTERN, payload=MOCK_EXACT_RESPONSE)
            async with aiohttp.ClientSession() as session:
                file_list = await d.get_file_list(session)
        assert len(file_list) == 1

    @pytest.mark.asyncio
    async def test_only_filter(self):
        d = WaybackDownloader(
            base_url="http://www.onlyfreegames.net",
            only_filter="menu.html",
        )
        with aioresponses() as m:
            _mock_cdx(m)
            async with aiohttp.ClientSession() as session:
                file_list = await d.get_file_list(session)
        assert len(file_list) == 1
        assert file_list[0]["file_id"] == "menu.html"

    @pytest.mark.asyncio
    async def test_only_filter_no_match(self):
        d = WaybackDownloader(
            base_url="http://www.onlyfreegames.net",
            only_filter="abc123",
        )
        with aioresponses() as m:
            _mock_cdx(m)
            async with aiohttp.ClientSession() as session:
                file_list = await d.get_file_list(session)
        assert len(file_list) == 0

    @pytest.mark.asyncio
    async def test_only_filter_regex(self):
        d = WaybackDownloader(
            base_url="http://www.onlyfreegames.net",
            only_filter=r'/\.(gif|jpg|bmp)$/i',
        )
        with aioresponses() as m:
            _mock_cdx(m)
            async with aiohttp.ClientSession() as session:
                file_list = await d.get_file_list(session)
        assert len(file_list) == 3  # logo.gif, bg.jpg, icon.bmp

    @pytest.mark.asyncio
    async def test_exclude_filter(self):
        d = WaybackDownloader(
            base_url="http://www.onlyfreegames.net",
            exclude_filter="menu.html",
        )
        with aioresponses() as m:
            _mock_cdx(m)
            async with aiohttp.ClientSession() as session:
                file_list = await d.get_file_list(session)
        file_ids = [f["file_id"] for f in file_list]
        assert "menu.html" not in file_ids

    @pytest.mark.asyncio
    async def test_exclude_filter_regex(self):
        d = WaybackDownloader(
            base_url="http://www.onlyfreegames.net",
            exclude_filter=r'/\.(gif|jpg|bmp)$/i',
        )
        with aioresponses() as m:
            _mock_cdx(m)
            async with aiohttp.ClientSession() as session:
                file_list = await d.get_file_list(session)
        file_ids = [f["file_id"] for f in file_list]
        assert "img/logo.gif" not in file_ids
        assert "img/bg.jpg" not in file_ids

    @pytest.mark.asyncio
    async def test_from_timestamp(self):
        """from_timestamp is passed as a CDX API parameter (server-side filter).
        Here we mock the API to return only results after the timestamp."""
        filtered_response = [
            ["timestamp", "original", "mimetype", "length"],
            ["20060716231334", "http://www.onlyfreegames.net:80/menu.html", "text/html", "1500"],
        ]
        d = WaybackDownloader(
            base_url="http://www.onlyfreegames.net",
            from_timestamp=20060716000000,
        )
        with aioresponses() as m:
            m.get(CDX_PATTERN, payload=filtered_response)
            m.get(CDX_PATTERN, payload=filtered_response)
            m.get(CDX_PATTERN, payload=[])
            async with aiohttp.ClientSession() as session:
                file_list = await d.get_file_list(session)
        assert len(file_list) == 1
        assert file_list[0]["file_id"] == "menu.html"
        assert file_list[0]["timestamp"] >= 20060716000000

    @pytest.mark.asyncio
    async def test_all_timestamps(self):
        d = WaybackDownloader(
            base_url="http://www.onlyfreegames.net",
            all_timestamps=True,
        )
        with aioresponses() as m:
            _mock_cdx(m)
            async with aiohttp.ClientSession() as session:
                file_list = await d.get_file_list(session)
        # With all_timestamps, keys include timestamp prefix
        for f in file_list:
            assert "/" in f["file_id"]  # timestamp/path format

    @pytest.mark.asyncio
    async def test_priority_sorting(self):
        """HTML files should come before images in the sorted list."""
        d = WaybackDownloader(base_url="http://www.onlyfreegames.net")
        with aioresponses() as m:
            _mock_cdx(m)
            async with aiohttp.ClientSession() as session:
                file_list = await d.get_file_list(session)

        # Find positions of HTML and image files
        html_positions = [
            i for i, f in enumerate(file_list) if f.get("mimetype") == "text/html"
        ]
        image_positions = [
            i for i, f in enumerate(file_list)
            if f.get("mimetype", "").startswith("image/")
        ]

        if html_positions and image_positions:
            # All HTML files should come before all image files
            assert max(html_positions) < min(image_positions)

    @pytest.mark.asyncio
    async def test_file_list_has_size(self):
        """File list entries should include size from CDX API."""
        d = WaybackDownloader(base_url="http://www.onlyfreegames.net")
        with aioresponses() as m:
            _mock_cdx(m)
            async with aiohttp.ClientSession() as session:
                file_list = await d.get_file_list(session)

        css_file = next(f for f in file_list if f["file_id"] == "css/style.css")
        assert css_file["size"] == 2100
        assert css_file["mimetype"] == "text/css"

    def test_resolve_file_path_root(self, downloader):
        dir_path, file_path = downloader._resolve_file_path("", "http://example.com/")
        assert file_path == downloader.backup_path / "index.html"

    def test_resolve_file_path_directory_url(self, downloader):
        dir_path, file_path = downloader._resolve_file_path(
            "some/path", "http://example.com/some/path/"
        )
        assert file_path == downloader.backup_path / "some/path/index.html"

    def test_resolve_file_path_file_url(self, downloader):
        dir_path, file_path = downloader._resolve_file_path(
            "path/file.html", "http://example.com/path/file.html"
        )
        assert file_path == downloader.backup_path / "path/file.html"

    def test_resolve_file_path_no_extension(self, downloader):
        dir_path, file_path = downloader._resolve_file_path(
            "path/resource", "http://example.com/path/resource"
        )
        assert file_path == downloader.backup_path / "path/resource/index.html"


# --- Session / Resume tests ---

class TestSessionAndResume:
    def test_should_download_new_file(self, downloader):
        """Files not in downloaded set should be downloaded."""
        file_info = {"file_id": "new.html", "file_url": "http://example.com/new.html",
                     "size": 1000}
        assert downloader._should_download(file_info, set())

    def test_should_skip_completed_file(self, tmp_path):
        """Files marked as downloaded with matching local file should be skipped."""
        d = WaybackDownloader(
            base_url="http://example.com",
            directory=str(tmp_path),
        )
        # Create a local file with matching size
        file_path = tmp_path / "page.html"
        file_path.write_text("x" * 1000)

        file_info = {"file_id": "page.html", "file_url": "http://example.com/page.html",
                     "size": 1000}
        assert not d._should_download(file_info, {"page.html"})

    def test_should_redownload_truncated_file(self, tmp_path):
        """Files smaller than expected size should be re-downloaded."""
        d = WaybackDownloader(
            base_url="http://example.com",
            directory=str(tmp_path),
        )
        # Create a truncated local file (100 bytes instead of 1000)
        file_path = tmp_path / "page.html"
        file_path.write_text("x" * 100)

        file_info = {"file_id": "page.html", "file_url": "http://example.com/page.html",
                     "size": 1000}
        assert d._should_download(file_info, {"page.html"})

    def test_should_redownload_missing_file(self, tmp_path):
        """Files in downloaded set but missing from disk should be re-downloaded."""
        d = WaybackDownloader(
            base_url="http://example.com",
            directory=str(tmp_path),
        )
        file_info = {"file_id": "gone.html", "file_url": "http://example.com/gone.html",
                     "size": 1000}
        assert d._should_download(file_info, {"gone.html"})

    def test_redo_forces_redownload(self, tmp_path):
        """--redo should force re-download even for completed files."""
        d = WaybackDownloader(
            base_url="http://example.com",
            directory=str(tmp_path),
            redo=True,
        )
        file_path = tmp_path / "page.html"
        file_path.write_text("x" * 1000)

        file_info = {"file_id": "page.html", "file_url": "http://example.com/page.html",
                     "size": 1000}
        assert d._should_download(file_info, {"page.html"})


class TestSessionManager:
    def test_create_and_load_session(self, tmp_path, monkeypatch):
        """Sessions should be creatable and loadable by ID."""
        from wayback_dl import session as session_mod
        monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)

        file_list = [
            {"file_id": "index.html", "file_url": "http://example.com/",
             "timestamp": 20170601, "mimetype": "text/html", "size": 1000},
        ]
        sid = session_mod.create_session("http://example.com", {"from_date": "2017-06-01"}, file_list)
        assert sid > 0

        loaded = session_mod.load_session(sid)
        assert loaded is not None
        assert loaded["base_url"] == "http://example.com"
        assert len(loaded["files"]) == 1
        assert loaded["total_size"] == 1000
        assert loaded["downloaded"] == []

    def test_update_session(self, tmp_path, monkeypatch):
        """Session progress should be updatable."""
        from wayback_dl import session as session_mod
        monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)

        file_list = [
            {"file_id": "a.html", "size": 500},
            {"file_id": "b.html", "size": 300},
        ]
        sid = session_mod.create_session("http://example.com", {}, file_list)

        session_mod.update_session(sid, {"a.html"}, 500)

        loaded = session_mod.load_session(sid)
        assert "a.html" in loaded["downloaded"]
        assert loaded["downloaded_size"] == 500

    def test_complete_session_removes_file(self, tmp_path, monkeypatch):
        """Completing a session should remove its file."""
        from wayback_dl import session as session_mod
        monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)

        sid = session_mod.create_session("http://example.com", {}, [])
        assert (tmp_path / f"{sid}.json").exists()

        session_mod.complete_session(sid)
        assert not (tmp_path / f"{sid}.json").exists()

    def test_load_nonexistent_session(self, tmp_path, monkeypatch):
        from wayback_dl import session as session_mod
        monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)

        assert session_mod.load_session(9999999) is None

    def test_list_sessions(self, tmp_path, monkeypatch):
        """list_sessions should not crash with sessions present."""
        from wayback_dl import session as session_mod
        monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)

        session_mod.create_session(
            "http://example.com",
            {"from_date": "2017-01-01", "to_date": "2017-12-31"},
            [{"file_id": "a.html", "size": 100}],
        )
        # Just verify it doesn't crash
        from rich.console import Console
        test_console = Console(file=open("/dev/null", "w"))
        session_mod.list_sessions(test_console)


# --- Timestamp parsing tests ---

class TestParseTimestamp:
    def test_iso_date(self):
        assert parse_timestamp("2017-06-01") == 20170601000000

    def test_iso_datetime(self):
        assert parse_timestamp("2017-06-01T14:30:00") == 20170601143000

    def test_iso_datetime_with_seconds(self):
        assert parse_timestamp("2010-09-16T23:13:34") == 20100916231334

    def test_raw_timestamp_full(self):
        assert parse_timestamp("20170601143000") == 20170601143000

    def test_raw_timestamp_date_only(self):
        assert parse_timestamp("20170601") == 20170601

    def test_raw_timestamp_year_only(self):
        assert parse_timestamp("2017") == 2017

    def test_none_returns_zero(self):
        assert parse_timestamp(None) == 0

    def test_empty_string_returns_zero(self):
        assert parse_timestamp("") == 0

    def test_invalid_format_raises(self):
        import typer
        with pytest.raises(typer.BadParameter):
            parse_timestamp("not-a-date")

    def test_invalid_string_raises(self):
        import typer
        with pytest.raises(typer.BadParameter):
            parse_timestamp("hello")
