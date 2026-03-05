"""Core downloader for fetching archived websites from the Wayback Machine.

This module contains the WaybackDownloader class which orchestrates the
entire download process:
1. Query the CDX API for a list of all archived files
2. Deduplicate and filter the file list
3. Download files concurrently with async I/O
4. Recreate the original directory structure locally

Features beyond basic downloading:
- Async concurrent downloads via aiohttp + asyncio.Semaphore
- Retry with exponential backoff on transient errors (tenacity)
- Rich progress bar with active download display
- Download prioritization (HTML first, images last)
- File type breakdown with sizes
- Resume support via .wayback_state.json checkpoint file
- Truncated file detection via CDX size comparison
"""

import asyncio
import json
import logging
import shutil
import time
from collections import defaultdict
from pathlib import Path

import aiofiles
import aiohttp
from rich.console import Console, Group
from rich.logging import RichHandler
from rich.panel import Panel
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeRemainingColumn,
    MofNCompleteColumn,
)
from rich.table import Table
from rich.live import Live
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from wayback_dl import VERSION
from wayback_dl.cdx_api import get_all_snapshots
from wayback_dl.session import (
    create_session,
    load_session,
    update_session,
    complete_session,
)
from wayback_dl.utils import (
    match_filter,
    decode_file_id,
    sanitize_path,
)

logger = logging.getLogger(__name__)

# Rich console outputs to stderr so that --list JSON output goes to stdout cleanly
console = Console(stderr=True)


# File type classification by mimetype, with download priority
# Lower number = downloaded first (HTML structure before heavy images)
FILE_CATEGORIES = {
    "HTML":   {"priority": 0, "mimetypes": {"text/html"}},
    "CSS":    {"priority": 1, "mimetypes": {"text/css"}},
    "JS":     {"priority": 2, "mimetypes": {
        "text/javascript", "application/javascript", "application/x-javascript",
    }},
    "Fonts":  {"priority": 3, "mimetypes": {
        "font/woff", "font/woff2", "font/ttf", "font/otf",
        "application/font-woff", "application/font-woff2",
        "application/x-font-woff", "application/x-font-ttf",
        "application/x-font-otf", "application/vnd.ms-fontobject",
    }},
    "Images": {"priority": 5, "mimetypes": set()},  # matched by prefix "image/"
}
OTHER_PRIORITY = 4  # "Other" goes between fonts and images


def _classify_file(mimetype: str) -> tuple[str, int]:
    """Classify a file by its mimetype, returning (category_name, priority)."""
    for name, info in FILE_CATEGORIES.items():
        if mimetype in info["mimetypes"]:
            return name, info["priority"]
    if mimetype.startswith("image/"):
        return "Images", FILE_CATEGORIES["Images"]["priority"]
    return "Other", OTHER_PRIORITY


def _format_size(size_bytes: int) -> str:
    """Format a byte count as a human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def setup_logging(verbose: bool = False) -> None:
    """Configure logging with Rich handler for pretty terminal output."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, show_path=False)],
    )


class WaybackDownloader:
    """Downloads archived websites from the Wayback Machine.

    Args:
        base_url: The website URL to download (e.g., 'http://example.com').
        directory: Custom download directory. Default: ./websites/<domain>
        all_timestamps: If True, download every snapshot version, not just the latest.
        from_timestamp: Only include snapshots on or after this timestamp.
        to_timestamp: Only include snapshots on or before this timestamp.
        exact_url: If True, only download the exact URL (no sub-pages/assets).
        only_filter: Include only URLs matching this filter (string or /regex/).
        exclude_filter: Exclude URLs matching this filter (string or /regex/).
        include_all: If True, include non-200 responses (errors, redirects).
        concurrency: Number of concurrent downloads. Default: 1.
        maximum_pages: Max CDX API pages to fetch (~150k snapshots/page).
        redo: If True, ignore previous state and re-download everything.
        session_id: Resume a specific session by ID.
        from_date: Original --from string for session metadata.
        to_date: Original --to string for session metadata.
    """

    def __init__(
        self,
        base_url: str,
        directory: str | None = None,
        all_timestamps: bool = False,
        from_timestamp: int = 0,
        to_timestamp: int = 0,
        exact_url: bool = False,
        only_filter: str | None = None,
        exclude_filter: str | None = None,
        include_all: bool = False,
        concurrency: int = 1,
        maximum_pages: int = 100,
        redo: bool = False,
        session_id: int | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ):
        self.base_url = base_url
        self.directory = directory
        self.all_timestamps = all_timestamps
        self.from_timestamp = from_timestamp
        self.to_timestamp = to_timestamp
        self.exact_url = exact_url
        self.only_filter = only_filter
        self.exclude_filter = exclude_filter
        self.include_all = include_all
        self.concurrency = max(1, concurrency)
        self.maximum_pages = maximum_pages
        self.redo = redo
        self.session_id = session_id
        self.from_date = from_date
        self.to_date = to_date

    @property
    def backup_name(self) -> str:
        """Extract the domain name from the base URL for use as directory name."""
        if '//' in self.base_url:
            return self.base_url.split('/')[2]
        return self.base_url

    @property
    def backup_path(self) -> Path:
        """Determine the local directory where files will be saved."""
        if self.directory:
            return Path(self.directory)
        return Path('websites') / self.backup_name

    def _curate_file_list(
        self, snapshots: list[list[str]]
    ) -> dict[str, dict]:
        """Deduplicate and filter the raw snapshot list from the CDX API.

        In normal mode: keeps only the latest timestamp for each unique file path.
        In all_timestamps mode: keeps every snapshot, keyed by '{timestamp}/{path}'.

        Also applies --only and --exclude filters to narrow results.
        """
        curated: dict[str, dict] = {}
        excluded_count = 0
        filtered_count = 0

        for entry in snapshots:
            # CDX API returns [timestamp, original, mimetype, length]
            timestamp_str = entry[0]
            file_url = entry[1]
            mimetype = entry[2] if len(entry) > 2 else ""
            length = entry[3] if len(entry) > 3 else "0"
            timestamp = int(timestamp_str)

            # Extract the relative file path from the full URL
            file_id = decode_file_id(file_url)
            if file_id is None:
                logger.debug("Malformed file url, ignoring: %s", file_url)
                continue

            # Apply exclude filter first (skip matching URLs)
            if match_filter(file_url, self.exclude_filter, exclude=True):
                excluded_count += 1
                continue
            # Apply include filter (skip non-matching URLs)
            if not match_filter(file_url, self.only_filter, exclude=False):
                filtered_count += 1
                continue

            # Parse length safely (CDX sometimes returns "-" or empty)
            try:
                size = int(length)
            except (ValueError, TypeError):
                size = 0

            if self.all_timestamps:
                # Keep every snapshot as a separate entry
                key = f"{timestamp_str}/{file_id}"
                if key not in curated:
                    curated[key] = {
                        "file_url": file_url,
                        "timestamp": timestamp,
                        "file_id": key,
                        "mimetype": mimetype,
                        "size": size,
                    }
            else:
                # Keep only the latest snapshot per unique file path
                if file_id not in curated or curated[file_id]["timestamp"] < timestamp:
                    curated[file_id] = {
                        "file_url": file_url,
                        "timestamp": timestamp,
                        "file_id": file_id,
                        "mimetype": mimetype,
                        "size": size,
                    }

        if excluded_count:
            logger.debug("Excluded by filter: %d snapshots", excluded_count)
        if filtered_count:
            logger.debug("Not matched by only filter: %d snapshots", filtered_count)

        return curated

    def _print_file_stats(self, file_list: list[dict]) -> None:
        """Print a file type breakdown table with counts and sizes."""
        stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "size": 0})

        for f in file_list:
            category, _ = _classify_file(f.get("mimetype", ""))
            stats[category]["count"] += 1
            stats[category]["size"] += f.get("size", 0)

        # Build a Rich table
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Type", style="bold")
        table.add_column("Count", justify="right")
        table.add_column("Size", justify="right", style="dim")

        # Sort by priority order
        priority_order = ["HTML", "CSS", "JS", "Fonts", "Other", "Images"]
        total_count = 0
        total_size = 0

        for category in priority_order:
            if category in stats:
                s = stats[category]
                table.add_row(category, f"{s['count']} files", _format_size(s["size"]))
                total_count += s["count"]
                total_size += s["size"]

        table.add_row("─" * 8, "─" * 8, "─" * 8, style="dim")
        table.add_row("Total", f"{total_count} files", _format_size(total_size), style="bold")

        console.print(table)
        console.print()

    @staticmethod
    def _sort_by_priority(file_list: list[dict]) -> list[dict]:
        """Sort files for download: HTML first, images last, small files first.

        Priority order: HTML → CSS → JS → Fonts → Other → Images
        Within each category, sorted by size ascending (smallest first).
        """
        def sort_key(f: dict) -> tuple[int, int]:
            _, priority = _classify_file(f.get("mimetype", ""))
            return (priority, f.get("size", 0))

        return sorted(file_list, key=sort_key)

    async def get_file_list(self, session: aiohttp.ClientSession) -> list[dict]:
        """Get the curated, deduplicated file list from the CDX API.

        Returns a list of dicts sorted by download priority, each with:
        - file_url: The original archived URL
        - timestamp: The snapshot timestamp (YYYYMMDDhhmmss as int)
        - file_id: The relative file path for local storage
        - mimetype: The MIME type from the CDX API
        - size: The response size in bytes from the CDX API
        """
        snapshots = await get_all_snapshots(
            session,
            self.base_url,
            exact_url=self.exact_url,
            maximum_pages=self.maximum_pages,
            include_all=self.include_all,
            from_timestamp=self.from_timestamp,
            to_timestamp=self.to_timestamp,
            progress_callback=lambda msg: console.print(msg),
        )

        curated = self._curate_file_list(snapshots)

        logger.debug(
            "Curated %d unique files from %d snapshots",
            len(curated), len(snapshots),
        )

        # Sort by priority (HTML first, images last) then by size (smallest first)
        file_list = self._sort_by_priority(list(curated.values()))
        return file_list

    async def list_files(self) -> None:
        """List files as JSON to stdout without downloading.

        Useful for debugging or piping to other tools. Status messages go
        to stderr (via Rich console) so stdout contains only clean JSON.
        """
        async with aiohttp.ClientSession() as session:
            file_list = await self.get_file_list(session)
            self._print_file_stats(file_list)
        print(json.dumps(file_list, indent=2))

    def _should_download(self, file_info: dict, downloaded: set[str]) -> bool:
        """Decide whether a file needs to be downloaded.

        Checks the state checkpoint and local filesystem to determine if
        a file is already complete, truncated, or missing.

        Returns True if the file should be (re-)downloaded.
        """
        file_id = file_info["file_id"]
        expected_size = file_info.get("size", 0)

        # --redo: always re-download
        if self.redo:
            return True

        # Check if marked as downloaded in state
        if file_id in downloaded:
            # Verify the local file still exists and matches expected size
            _, file_path = self._resolve_file_path(file_id, file_info["file_url"])
            if file_path.exists():
                local_size = file_path.stat().st_size
                if local_size == 0 and not self.include_all:
                    # Empty file — was probably cleaned up, re-download
                    logger.debug("Re-downloading (empty): %s", file_id)
                    return True
                if expected_size > 0 and local_size < expected_size:
                    # Truncated file — re-download
                    logger.debug(
                        "Re-downloading (truncated %s < %s): %s",
                        _format_size(local_size), _format_size(expected_size), file_id,
                    )
                    return True
                # File exists and looks complete
                logger.debug("Skipped (complete): %s", file_id)
                return False
            else:
                # State says downloaded but file is missing — re-download
                logger.debug("Re-downloading (missing): %s", file_id)
                return True

        # Not in state — check filesystem as fallback
        _, file_path = self._resolve_file_path(file_id, file_info["file_url"])
        if file_path.exists() and file_path.stat().st_size > 0:
            local_size = file_path.stat().st_size
            if expected_size > 0 and local_size < expected_size:
                logger.debug(
                    "Re-downloading (truncated %s < %s): %s",
                    _format_size(local_size), _format_size(expected_size), file_id,
                )
                return True
            logger.debug("Skipped (exists): %s", file_id)
            return False

        return True

    def _get_session_params(self) -> dict:
        """Build a dict of CLI parameters for session metadata."""
        return {
            "directory": self.directory,
            "from_date": self.from_date,
            "to_date": self.to_date,
            "exact_url": self.exact_url,
            "all_timestamps": self.all_timestamps,
            "only_filter": self.only_filter,
            "exclude_filter": self.exclude_filter,
            "include_all": self.include_all,
            "concurrency": self.concurrency,
            "maximum_pages": self.maximum_pages,
        }

    async def download(self) -> None:
        """Download all files from the Wayback Machine.

        Workflow:
        1. Load session (if resuming) or query CDX API and create new session
        2. Filter out already-completed files (unless --redo)
        3. Download remaining files concurrently with progress bar
        4. Update session after each file completes
        5. Remove session on full completion
        """
        async with aiohttp.ClientSession() as http_session:
            # Try to resume an existing session
            session_data = None
            current_session_id = self.session_id

            if current_session_id and not self.redo:
                session_data = load_session(current_session_id)
                if session_data:
                    file_list = session_data["files"]
                    downloaded = set(session_data.get("downloaded", []))
                    # Restore base_url from session if not provided on CLI
                    if not self.base_url:
                        self.base_url = session_data["base_url"]
                    console.print(
                        f"Resuming session {current_session_id}: "
                        f"{len(downloaded)}/{len(file_list)} files already downloaded"
                    )
                else:
                    console.print(f"Session {current_session_id} not found.")
                    return

            if self.redo and current_session_id:
                console.print("--redo: ignoring previous state, re-downloading all files")
                session_data = None
                current_session_id = None

            if not session_data:
                # Fresh download — query CDX API
                file_list = await self.get_file_list(http_session)
                downloaded = set()

            if not file_list:
                console.print("No files to download.")
                if self.from_timestamp:
                    console.print("  - From timestamp may be too far in the future.")
                if self.to_timestamp:
                    console.print("  - To timestamp may be too far in the past.")
                if self.only_filter:
                    console.print(f"  - Only filter may be too restrictive: {self.only_filter}")
                if self.exclude_filter:
                    console.print(f"  - Exclude filter may be too broad: {self.exclude_filter}")
                return

            # Determine which files still need downloading
            pending = [f for f in file_list if self._should_download(f, downloaded)]

            console.print(f"wayback-dl {VERSION}")
            console.print(
                f"Downloading {len(file_list)} files from {self.base_url} "
                f"to {self.backup_path}/"
            )
            if len(pending) < len(file_list):
                skipped = len(file_list) - len(pending)
                console.print(f"  {skipped} files already complete, {len(pending)} remaining")
            console.print()
            self._print_file_stats(file_list)

            if not pending:
                console.print("All files already downloaded. Use --redo to force re-download.")
                if current_session_id:
                    complete_session(current_session_id)
                return

            # Create a new session if we don't have one
            if not current_session_id:
                current_session_id = create_session(
                    self.base_url, self._get_session_params(), file_list,
                )
                console.print(f"Session: {current_session_id}")

            # Track downloaded bytes for session updates
            downloaded_size = sum(
                f.get("size", 0) for f in file_list
                if f["file_id"] in downloaded
            )

            # Use a semaphore to limit concurrent downloads
            semaphore = asyncio.Semaphore(self.concurrency)

            # Track active downloads for display
            active_slots: dict[int, str] = {}
            slot_lock = asyncio.Lock()
            next_slot = [0]

            # Overall progress bar
            overall_progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TaskProgressColumn(),
                TimeRemainingColumn(),
                console=console,
                expand=True,
            )
            overall_task = overall_progress.add_task(
                "Downloading", total=len(pending)
            )

            def _build_display() -> Group:
                """Build the combined display: progress bar + active downloads."""
                parts = [overall_progress]
                if active_slots:
                    lines = []
                    for slot_id in sorted(active_slots.keys()):
                        file_label = active_slots[slot_id]
                        lines.append(f"  [dim]→[/dim] {file_label}")
                    parts.append(Panel(
                        "\n".join(lines),
                        title=f"[bold]Active ({len(active_slots)})[/bold]",
                        border_style="dim",
                        expand=True,
                    ))
                return Group(*parts)

            # Flag to signal graceful abort
            aborted = False

            with Live(
                _build_display(),
                console=console,
                refresh_per_second=4,
            ) as live:

                async def download_with_tracking(file_info: dict) -> None:
                    nonlocal downloaded_size
                    if aborted:
                        return

                    async with slot_lock:
                        slot = next_slot[0]
                        next_slot[0] += 1

                    file_label = file_info["file_id"] or "index.html"
                    size = file_info.get("size", 0)
                    if size:
                        file_label += f" ({_format_size(size)})"

                    async with semaphore:
                        if aborted:
                            return
                        active_slots[slot] = file_label
                        live.update(_build_display())
                        try:
                            await self._download_file(http_session, file_info)
                            # Mark as downloaded
                            downloaded.add(file_info["file_id"])
                            downloaded_size += file_info.get("size", 0)
                            update_session(current_session_id, downloaded, downloaded_size)
                        except asyncio.CancelledError:
                            return
                        except Exception as e:
                            # File failed after all retries — skip it, don't crash
                            file_id = file_info["file_id"] or "index.html"
                            logger.warning("Skipped (failed after retries): %s — %s", file_id, e)
                        finally:
                            active_slots.pop(slot, None)

                    overall_progress.update(overall_task, advance=1)
                    live.update(_build_display())

                try:
                    tasks = [download_with_tracking(f) for f in pending]
                    await asyncio.gather(*tasks)
                except KeyboardInterrupt:
                    aborted = True
                except asyncio.CancelledError:
                    aborted = True

            if aborted:
                # Save final state before exiting
                update_session(current_session_id, downloaded, downloaded_size)
                done = len(downloaded)
                total = len(file_list)
                console.print()
                console.print(f"[bold yellow]Aborted.[/bold yellow] "
                              f"{done}/{total} files downloaded.")
                console.print()
                console.print("[bold]To resume:[/bold]")
                console.print(f"  wayback-dl -s {current_session_id}")
                return

            # All done — remove session
            complete_session(current_session_id)

            console.print(
                f"Download complete: {len(file_list)} files saved to {self.backup_path}/"
            )

    def _resolve_file_path(self, file_id: str, file_url: str) -> tuple[Path, Path]:
        """Map a file ID and URL to local directory and file paths.

        Handles three cases:
        - Empty file_id (root URL) -> saves as index.html
        - Directory-style URL (ends with / or no file extension) -> saves as path/index.html
        - File URL (has extension) -> saves as-is

        Returns (dir_path, file_path) tuple, sanitized for the current platform.
        Raises ValueError if the resolved path escapes the backup directory.
        """
        if not file_id:
            return self.backup_path, self.backup_path / "index.html"

        # Sanitize the file_id before building paths (strips .., control chars)
        safe_id = sanitize_path(file_id)

        if file_url.endswith('/') or '.' not in safe_id.split('/')[-1]:
            dir_path = self.backup_path / safe_id
            file_path = dir_path / "index.html"
        else:
            parts = safe_id.split('/')
            dir_path = self.backup_path / '/'.join(parts[:-1]) if len(parts) > 1 else self.backup_path
            file_path = self.backup_path / safe_id

        # SECURITY: Validate resolved path stays within backup directory
        resolved = file_path.resolve()
        backup_resolved = self.backup_path.resolve()
        if not str(resolved).startswith(str(backup_resolved) + '/') and resolved != backup_resolved:
            raise ValueError(f"Path traversal detected, skipping: {file_id}")

        return dir_path, file_path

    def _structure_dir_path(self, dir_path: Path) -> None:
        """Create directory structure, resolving file-to-directory conflicts.

        When a file already exists at a path that needs to become a directory
        (e.g., 'about' exists as a file but we need 'about/page.html'),
        the existing file is moved to 'about/index.html'.

        SECURITY: Checks for symlinks to prevent symlink-following attacks.
        """
        # Check for symlinks in the path chain
        check = dir_path
        while check != self.backup_path and check != check.parent:
            if check.is_symlink():
                raise ValueError(f"Symlink detected in path, skipping: {check}")
            check = check.parent

        try:
            dir_path.mkdir(parents=True, exist_ok=True)
        except (FileExistsError, NotADirectoryError):
            # Walk up to find the conflicting file
            conflicting = dir_path
            while not conflicting.exists():
                conflicting = conflicting.parent
            if conflicting.is_symlink():
                raise ValueError(f"Symlink detected in path, skipping: {conflicting}")
            if conflicting.is_file():
                # Move the file into a new directory as index.html
                temp_path = conflicting.with_suffix('.temp')
                shutil.move(str(conflicting), str(temp_path))
                conflicting.mkdir(parents=True, exist_ok=True)
                shutil.move(str(temp_path), str(conflicting / 'index.html'))
                logger.info("%s -> %s/index.html", conflicting, conflicting)
            dir_path.mkdir(parents=True, exist_ok=True)

    # Download URL must start with this prefix (prevents SSRF if CDX data is tampered)
    WAYBACK_DOWNLOAD_PREFIX = "https://web.archive.org/web/"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((aiohttp.ClientError, TimeoutError)),
    )
    async def _download_file(
        self, session: aiohttp.ClientSession, file_info: dict
    ) -> None:
        """Download a single file from the Wayback Machine.

        Downloads from: https://web.archive.org/web/{timestamp}id_/{url}
        The 'id_' suffix tells the Wayback Machine to return the original
        unmodified file (no toolbar injection, no URL rewriting).

        SECURITY: Uses streaming writes to avoid buffering large files in memory.
        Validates download URL points to web.archive.org.
        Checks for symlinks before writing.

        Retries up to 3 times with exponential backoff on transient errors.
        """
        file_url = file_info["file_url"]
        file_id = file_info["file_id"]
        timestamp = file_info["timestamp"]

        dir_path, file_path = self._resolve_file_path(file_id, file_url)
        self._structure_dir_path(dir_path)

        # SECURITY: Check for symlinks at the write target
        if file_path.is_symlink():
            logger.warning("Symlink detected, skipping: %s", file_path)
            return

        # The 'id_' suffix is critical — it fetches the original file
        download_url = f"https://web.archive.org/web/{timestamp}id_/{file_url}"

        # SECURITY: Validate download URL points to archive.org
        if not download_url.startswith(self.WAYBACK_DOWNLOAD_PREFIX):
            logger.warning("Invalid download URL, skipping: %s", download_url)
            return

        start = time.monotonic()

        try:
            async with session.get(
                download_url,
                headers={"Accept-Encoding": "identity"},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status == 200 or (self.include_all and resp.status != 200):
                    # Stream response to disk in chunks to avoid unbounded memory usage
                    total_written = 0
                    async with aiofiles.open(file_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            await f.write(chunk)
                            total_written += len(chunk)
                    elapsed = time.monotonic() - start
                    if resp.status == 200:
                        logger.debug(
                            "Downloaded: %s (%s, %d, %.1fs)",
                            file_id or "index.html",
                            _format_size(total_written), resp.status, elapsed,
                        )
                    else:
                        logger.debug(
                            "Saved (status %d): %s (%s)",
                            resp.status, file_id, _format_size(total_written),
                        )
                else:
                    logger.debug("Skipped (status %d): %s", resp.status, file_url)
                    return
        except Exception as e:
            logger.debug("Failed: %s — %s", file_url, e)
            raise

        # Clean up empty files (unless --all flag is set)
        if not self.include_all and file_path.exists() and file_path.stat().st_size == 0:
            file_path.unlink()
            logger.debug("Removed empty file: %s", file_path)

