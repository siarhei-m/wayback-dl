"""Command-line interface for wayback_dl.

Uses Typer for argument parsing with type hints. The CLI entry point
is registered as 'wayback-dl' in pyproject.toml.

Usage:
    wayback-dl http://example.com
    wayback-dl http://example.com --concurrency 10 --list
    wayback-dl --list-sessions
    wayback-dl http://example.com --session 1709571234
    python -m wayback_dl http://example.com
"""

import asyncio
from datetime import datetime
from typing import Optional

import typer
from typing_extensions import Annotated

from wayback_dl import VERSION
from wayback_dl.downloader import WaybackDownloader, setup_logging, console
from wayback_dl.session import list_sessions
from wayback_dl.utils import validate_url


CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}

app = typer.Typer(
    help="Download an entire website from the Internet Archive Wayback Machine.",
    add_completion=False,
    context_settings=CONTEXT_SETTINGS,
)


def version_callback(value: bool) -> None:
    """Print version and exit when --version is passed."""
    if value:
        print(f"wayback_dl {VERSION}")
        raise typer.Exit()


def list_sessions_callback(value: bool) -> None:
    """List all active/interrupted sessions and exit."""
    if value:
        setup_logging(verbose=False)
        list_sessions(console)
        raise typer.Exit()


def parse_timestamp(value: str | None) -> int:
    """Parse a timestamp string into CDX API format (YYYYMMDDhhmmss).

    Accepts:
        - ISO 8601 date: '2017-06-01'
        - ISO 8601 datetime: '2017-06-01T14:30:00'
        - Raw CDX timestamp: '20170601' or '20170601143000'
        - None or empty string: returns 0 (no filter)
    """
    if not value:
        return 0

    # Try ISO 8601 format first (contains '-')
    if '-' in value:
        try:
            dt = datetime.fromisoformat(value)
            return int(dt.strftime('%Y%m%d%H%M%S'))
        except ValueError:
            raise typer.BadParameter(
                f"Invalid date format: '{value}'. "
                "Use ISO 8601 (e.g., 2017-06-01 or 2017-06-01T14:30:00) "
                "or raw timestamp (e.g., 20170601)."
            )

    # Otherwise treat as raw CDX timestamp (digits only)
    if value.isdigit():
        return int(value)

    raise typer.BadParameter(
        f"Invalid timestamp: '{value}'. "
        "Use ISO 8601 (e.g., 2017-06-01) or raw timestamp (e.g., 20170601)."
    )


@app.command(context_settings=CONTEXT_SETTINGS)
def main(
    url: Annotated[Optional[str], typer.Argument(
        help="The URL of the website to download",
    )] = None,
    directory: Annotated[Optional[str], typer.Option(
        "--directory", "-d",
        help="Directory to save downloaded files into. Default: ./websites/<domain>",
    )] = None,
    all_timestamps: Annotated[bool, typer.Option(
        "--all-timestamps", "-A",
        help="Download all snapshots/timestamps for a given website",
    )] = False,
    from_date: Annotated[Optional[str], typer.Option(
        "--from", "-f",
        help="Only files on or after this date (e.g., 2006-07-16 or 20060716231334)",
    )] = None,
    to_date: Annotated[Optional[str], typer.Option(
        "--to", "-t",
        help="Only files on or before this date (e.g., 2010-09-16 or 20100916231334)",
    )] = None,
    exact_url: Annotated[bool, typer.Option(
        "--exact-url", "-e",
        help="Download only the exact URL provided, not the full site",
    )] = False,
    only: Annotated[Optional[str], typer.Option(
        "--only", "-o",
        help="Restrict to URLs matching this filter (use /regex/ notation for regex)",
    )] = None,
    exclude: Annotated[Optional[str], typer.Option(
        "--exclude", "-x",
        help="Skip URLs matching this filter (use /regex/ notation for regex)",
    )] = None,
    include_all: Annotated[bool, typer.Option(
        "--all", "-a",
        help="Include error pages (40x, 50x) and redirects (30x)",
    )] = False,
    concurrency: Annotated[int, typer.Option(
        "--concurrency", "-c",
        help="Number of concurrent downloads. Default: 1",
    )] = 1,
    max_pages: Annotated[int, typer.Option(
        "--max-pages", "-p",
        help="Maximum snapshot pages to consider (~150k snapshots/page). Default: 100",
    )] = 100,
    list_only: Annotated[bool, typer.Option(
        "--list", "-l",
        help="Only list file URLs as JSON, don't download",
    )] = False,
    session_id: Annotated[Optional[int], typer.Option(
        "--session", "-s",
        help="Resume a previous download session by ID",
    )] = None,
    list_sessions_flag: Annotated[bool, typer.Option(
        "--list-sessions", "-S",
        help="List all active/interrupted download sessions",
        callback=list_sessions_callback,
        is_eager=True,
    )] = False,
    redo: Annotated[bool, typer.Option(
        "--redo", "-r",
        help="Force re-download all files, ignoring previous progress",
    )] = False,
    verbose: Annotated[bool, typer.Option(
        "--verbose", "-v",
        help="Enable verbose/debug logging",
    )] = False,
    version: Annotated[bool, typer.Option(
        "--version", "-V",
        help="Show version and exit",
        callback=version_callback,
        is_eager=True,
    )] = False,
) -> None:
    """Download an entire website from the Wayback Machine."""
    setup_logging(verbose=verbose)

    # When resuming a session, URL is optional (loaded from session)
    if not url and not session_id:
        console.print("Error: URL is required unless resuming a session with --session")
        raise typer.Exit(1)

    # Validate URL scheme (only http/https allowed)
    if url:
        try:
            validate_url(url)
        except ValueError as e:
            console.print(f"Error: {e}")
            raise typer.Exit(1)

    downloader = WaybackDownloader(
        base_url=url or "",
        directory=directory,
        all_timestamps=all_timestamps,
        from_timestamp=parse_timestamp(from_date),
        to_timestamp=parse_timestamp(to_date),
        exact_url=exact_url,
        only_filter=only,
        exclude_filter=exclude,
        include_all=include_all,
        concurrency=concurrency,
        maximum_pages=max_pages,
        redo=redo,
        session_id=session_id,
        # Store original string params for session metadata
        from_date=from_date,
        to_date=to_date,
    )

    if list_only:
        asyncio.run(downloader.list_files())
    else:
        try:
            asyncio.run(downloader.download())
        except KeyboardInterrupt:
            # The downloader's internal handler should have printed the
            # resume message already, but if Ctrl+C happened during CDX
            # query or before download started, just exit cleanly.
            pass
