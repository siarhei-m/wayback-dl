# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python CLI tool (`wayback-dl`) that downloads entire websites from the Internet Archive Wayback Machine. It fetches file listings via the CDX API (including mimetype and size), then downloads original (non-rewritten) files with async I/O, retry logic, progress tracking, and session-based resume support.

## Commands

```bash
# Create virtual environment and install dependencies
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# Install globally
uv tool install --force /path/to/wayback-dl

# Run all tests (65 tests)
pytest tests/ -v

# Run a single test class or method
pytest tests/test_downloader.py::TestSessionManager -v
pytest tests/test_downloader.py::TestWaybackDownloader::test_priority_sorting -v

# Run the CLI locally
wayback-dl http://example.com
wayback-dl http://example.com --from 2017-01-01 --to 2017-12-31 -c 5
```

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed architecture documentation, including data flow, design decisions, and component descriptions.

Key point: this tool does **not** parse HTML or crawl pages. It uses the Wayback Machine CDX API to get a complete file index, then downloads original files directly.

## Key Details

- Tests use pytest with aioresponses for mocking — no live API calls, fast and deterministic.
- The test fixture data is based on `http://www.onlyfreegames.net`.
- Version is defined in `src/wayback_dl/__init__.py`.
- Requires Python >= 3.11. Key dependencies: aiohttp, tenacity, rich, aiofiles, typer.
- Files are downloaded from `https://web.archive.org/web/{timestamp}id_/{url}` (the `id_` suffix fetches the original, unmodified file).
- Sessions stored in `~/.wayback_dl/sessions/<unix_timestamp>.json` — track per-file completion, support parallel downloads of different domains.
- Download priority: HTML → CSS → JS → Fonts → Other → Images (smallest first within each category).
- Graceful Ctrl+C: saves progress and prints `wayback-dl -s <session_id>` resume command.

## CLI Aliases

| Short | Long | Description |
|---|---|---|
| `-h` | `--help` | Show help |
| `-V` | `--version` | Show version |
| `-v` | `--verbose` | Debug logging |
| `-d` | `--directory` | Output directory |
| `-f` | `--from` | Start date (ISO 8601 or raw timestamp) |
| `-t` | `--to` | End date |
| `-e` | `--exact-url` | Download only the exact URL |
| `-o` | `--only` | Include filter |
| `-x` | `--exclude` | Exclude filter |
| `-a` | `--all` | Include error/redirect pages |
| `-A` | `--all-timestamps` | Download all snapshot versions |
| `-c` | `--concurrency` | Concurrent downloads |
| `-p` | `--max-pages` | Max CDX pages |
| `-l` | `--list` | List files as JSON |
| `-s` | `--session` | Resume session by ID |
| `-r` | `--redo` | Force re-download |
| `-S` | `--list-sessions` | Show all sessions |
