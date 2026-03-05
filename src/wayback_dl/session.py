"""Session management for tracking download state across runs.

Sessions are stored in ~/.wayback_dl/sessions/<unix_timestamp>.json.
Each session tracks the full file list, download parameters, and
per-file completion status. This allows:
- Parallel downloads of different domains/date ranges
- Resuming interrupted downloads by session ID
- Listing all past and in-progress sessions

SECURITY:
- Sessions directory created with 0o700 permissions (owner-only access)
- Session files written atomically via temp file + rename to prevent
  corruption on crash/interrupt
"""

import json
import logging
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)

# Central session storage directory
SESSIONS_DIR = Path.home() / ".wayback_dl" / "sessions"

# Current session file format version
SESSION_VERSION = 3


def _sessions_dir() -> Path:
    """Get (and create if needed) the sessions directory.

    SECURITY: Directory created with 0o700 (owner read/write/execute only)
    to prevent other users on shared systems from reading session data.
    """
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    # Ensure restrictive permissions (no group/other access)
    try:
        os.chmod(SESSIONS_DIR, 0o700)
    except OSError:
        pass  # May fail on some filesystems (e.g., FAT32/NTFS on Windows)
    return SESSIONS_DIR


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON data to a file atomically.

    SECURITY: Uses write-to-temp-then-rename pattern to prevent corruption
    if the process is interrupted (Ctrl+C, crash, power loss) during write.
    On POSIX systems, rename is atomic within the same filesystem.
    """
    dir_path = path.parent
    try:
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp')
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f)
        Path(tmp_path).replace(path)
    except OSError:
        # Fallback to direct write if temp file fails (e.g., cross-device)
        with open(path, 'w') as f:
            json.dump(data, f)


def create_session(
    base_url: str,
    params: dict,
    file_list: list[dict],
) -> int:
    """Create a new session and save it to disk.

    Args:
        base_url: The website URL being downloaded.
        params: CLI parameters used for this run.
        file_list: The curated file list from the CDX API.

    Returns:
        The session ID (unix timestamp).
    """
    session_id = int(time.time())
    total_size = sum(f.get("size", 0) for f in file_list)

    session = {
        "version": SESSION_VERSION,
        "id": session_id,
        "created": datetime.now().isoformat(timespec="seconds"),
        "base_url": base_url,
        "params": params,
        "files": file_list,
        "downloaded": [],
        "total_size": total_size,
        "downloaded_size": 0,
    }

    path = _sessions_dir() / f"{session_id}.json"
    _atomic_write(path, session)

    logger.debug("Created session %d (%d files)", session_id, len(file_list))
    return session_id


def load_session(session_id: int) -> dict | None:
    """Load a session by its ID.

    Returns the session dict or None if not found/invalid.
    """
    path = _sessions_dir() / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            session = json.load(f)
        if session.get("version") != SESSION_VERSION:
            logger.debug("Session %d has incompatible version %s", session_id, session.get("version"))
            return None
        return session
    except (json.JSONDecodeError, OSError) as e:
        logger.debug("Failed to load session %d: %s", session_id, e)
        return None


def update_session(session_id: int, downloaded: set[str], downloaded_size: int) -> None:
    """Update a session's download progress.

    Uses atomic write to prevent corruption on interrupt.

    Args:
        session_id: The session ID.
        downloaded: Set of file_ids that have been downloaded.
        downloaded_size: Total bytes downloaded so far.
    """
    path = _sessions_dir() / f"{session_id}.json"
    if not path.exists():
        return

    try:
        with open(path) as f:
            session = json.load(f)
    except (json.JSONDecodeError, OSError):
        return

    session["downloaded"] = list(downloaded)
    session["downloaded_size"] = downloaded_size

    _atomic_write(path, session)


def complete_session(session_id: int) -> None:
    """Mark a session as complete by removing its file.

    Completed sessions are deleted to keep the sessions directory clean.
    Only in-progress/interrupted sessions remain.
    """
    path = _sessions_dir() / f"{session_id}.json"
    if path.exists():
        path.unlink()
        logger.debug("Session %d completed and removed", session_id)


def list_sessions(console: Console) -> None:
    """List all active/interrupted sessions as a Rich table.

    Shows session ID, domain, date range, file count, and progress.
    """
    sessions_dir = _sessions_dir()
    session_files = sorted(sessions_dir.glob("*.json"))

    if not session_files:
        console.print("No active sessions found.")
        return

    table = Table(title="Download Sessions")
    table.add_column("Session ID", style="bold cyan")
    table.add_column("Created")
    table.add_column("Domain")
    table.add_column("Date Range")
    table.add_column("Files", justify="right")
    table.add_column("Progress", justify="right")
    table.add_column("Directory")

    for sf in session_files:
        try:
            with open(sf) as f:
                session = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        if session.get("version") != SESSION_VERSION:
            continue

        session_id = session.get("id", sf.stem)
        created = session.get("created", "?")
        # Shorten ISO datetime for display
        if "T" in str(created):
            created = str(created).replace("T", " ")

        base_url = session.get("base_url", "?")
        # Extract domain from URL
        if "//" in base_url:
            domain = base_url.split("/")[2]
        else:
            domain = base_url

        params = session.get("params", {})
        from_ts = params.get("from_date", "")
        to_ts = params.get("to_date", "")
        if from_ts and to_ts:
            date_range = f"{from_ts}..{to_ts}"
        elif from_ts:
            date_range = f"{from_ts}.."
        elif to_ts:
            date_range = f"..{to_ts}"
        else:
            date_range = "(all)"

        files = session.get("files", [])
        downloaded = session.get("downloaded", [])
        total = len(files)
        done = len(downloaded)

        if total > 0:
            pct = done / total * 100
            progress = f"{done}/{total} ({pct:.1f}%)"
        else:
            progress = "0/0"

        directory = params.get("directory", "")
        if not directory:
            directory = f"websites/{domain}"

        table.add_row(
            str(session_id),
            created,
            domain,
            date_range,
            str(total),
            progress,
            directory,
        )

    console.print(table)
    console.print()
    console.print("[dim]Resume a session: wayback-dl --session SESSION_ID[/dim]")
