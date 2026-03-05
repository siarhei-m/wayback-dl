# Architecture

## How It Works (The Big Picture)

This tool does **not** crawl websites or parse HTML. It never extracts links, images, JS, or CSS from page content.

Instead, it relies entirely on the **Wayback Machine CDX API** — an index that archive.org maintains of every URL it has ever archived. The CDX API already knows about every file (HTML, JS, CSS, images, etc.) under a given domain.

### Data Flow

```
1. QUERY CDX API          2. FILTER & DEDUPLICATE        3. DOWNLOAD FILES

web.archive.org/cdx/   →  Keep latest timestamp      →  Fetch original files from
search/cdx?url=site/*     per unique file path           web.archive.org/web/
                           Apply --only/--exclude         {timestamp}id_/{url}
Returns JSON array of      filters
[timestamp, url,                                         The "id_" suffix bypasses
 mimetype, length]        Classify by mimetype            Wayback Machine's URL
                           (HTML/CSS/JS/Fonts/            rewriting, returning the
Paginated (up to 100       Images/Other)                  original unmodified file
pages, ~150k snapshots
per page)                 Sort by priority:              Download priority:
                           HTML first → Images last       small files first
```

### Key Design Decisions

- **No HTML parsing or crawling** — The CDX API provides a complete file index, so there is no need to parse page content to discover assets. This is simpler and more reliable than crawling.
- **Deduplication by file path** — When multiple snapshots exist for the same URL, only the latest timestamp is kept (unless `--all-timestamps` is used).
- **Original file retrieval** — The `id_` segment in the download URL (`/web/{timestamp}id_/{url}`) tells archive.org to serve the original file without injecting its toolbar or rewriting URLs.
- **Directory structure recreation** — URLs ending in `/` or without a file extension are saved as `{path}/index.html` to work with static web servers. When a file path collides with a directory path, the file is moved to `{path}/index.html`.
- **Async I/O with concurrency control** — Uses aiohttp + asyncio.Semaphore for efficient concurrent downloads without thread overhead.
- **Retry with exponential backoff** — CDX API and file download requests retry on transient errors (network issues, 429 rate limits, 5xx server errors) using tenacity.
- **Download prioritization** — Files are sorted by type (HTML → CSS → JS → Fonts → Other → Images) and by size (smallest first), so the site structure is usable quickly.
- **Session-based resume** — Each download creates a session in `~/.wayback_dl/sessions/` that tracks per-file completion status. Interrupted downloads resume from where they stopped. Multiple parallel downloads of different domains don't conflict.
- **Truncated file detection** — On resume, files smaller than the CDX expected size are re-downloaded automatically.
- **Graceful abort** — Ctrl+C saves progress and prints the exact command to resume.

## Components

### CLI (`src/wayback_dl/cli.py`)

Typer-based CLI that parses command-line options, constructs a `WaybackDownloader` instance, and runs either `list_files()` (JSON output) or `download()` via `asyncio.run()`. Handles `--list-sessions` and `--session` for session management. Parses `--from`/`--to` dates using `datetime.fromisoformat()` (ISO 8601) with fallback to raw CDX timestamps.

### Core Downloader (`src/wayback_dl/downloader.py`)

The `WaybackDownloader` class orchestrates the entire process:

1. **`get_file_list()`** — Queries the CDX API via `get_all_snapshots()`, then calls `_curate_file_list()` to deduplicate, filter, and `_sort_by_priority()` to order for download.
2. **`_curate_file_list()`** — Deduplicates snapshots by file path (keeps latest timestamp). Applies `--only` and `--exclude` filters. Extracts mimetype and size from CDX data. In `--all-timestamps` mode, keeps every snapshot keyed by `{timestamp}/{path}`.
3. **`_print_file_stats()`** — Displays a Rich table breaking down files by type (HTML/CSS/JS/Fonts/Images/Other) with counts and total sizes.
4. **`_sort_by_priority()`** — Sorts files: HTML first (priority 0) → CSS (1) → JS (2) → Fonts (3) → Other (4) → Images (5). Within each category, smallest files first.
5. **`_should_download()`** — Checks session state and filesystem to decide if a file needs downloading. Detects truncated files by comparing local size vs CDX expected size.
6. **`download()`** — Main download loop. Loads or creates a session, filters completed files, spawns concurrent tasks with `asyncio.Semaphore`, shows Rich Live display with progress bar and active downloads panel. Handles Ctrl+C gracefully.
7. **`_download_file()`** — Downloads a single file from `web.archive.org` with retry logic (3 attempts, exponential backoff).
8. **`_resolve_file_path()`** — Maps a file URL to a local directory and file path. Handles directory-style URLs by appending `index.html`.
9. **`_structure_dir_path()`** — Creates directories, resolving conflicts where a file exists at a path that needs to become a directory.

### Session Manager (`src/wayback_dl/session.py`)

Manages download sessions stored in `~/.wayback_dl/sessions/<unix_timestamp>.json`. Each session file contains:

- **version**: Schema version for forward compatibility
- **id**: Unix timestamp session ID
- **created**: ISO 8601 creation time
- **base_url**: The website URL being downloaded
- **params**: CLI parameters used (directory, date range, filters, concurrency, etc.)
- **files**: The full curated file list from CDX API
- **downloaded**: List of file_ids successfully downloaded
- **total_size / downloaded_size**: Byte counts for progress tracking

Key functions:
- **`create_session()`** — Creates a new session file
- **`load_session()`** — Loads a session by ID, validates schema version
- **`update_session()`** — Updates downloaded list and size after each file
- **`complete_session()`** — Removes session file on successful completion
- **`list_sessions()`** — Displays a Rich table of all active/interrupted sessions

### CDX API Client (`src/wayback_dl/cdx_api.py`)

Async CDX API client with two main functions:

- **`get_all_snapshots()`** — Fetches all snapshots for a URL. First queries the exact URL, then queries `url/*` with pagination to get all files under the domain. Stops pagination when a page returns empty results.
- **`_fetch_snapshot_page()`** — Makes a single CDX API request with tenacity retry (5 attempts, exponential backoff). Returns a list of `[timestamp, original_url, mimetype, length]` entries. Handles 429 rate limiting and 5xx errors as retryable. Logs request URL, response time, and result count at DEBUG level.

Requests fields: `timestamp,original,mimetype,length` — the mimetype and length are used for file type classification, size display, and download prioritization.

### Utilities (`src/wayback_dl/utils.py`)

- **`to_regex()`** — Parses `/pattern/flags` notation into a compiled `re.Pattern`. Returns `None` for plain strings (triggering substring matching instead).
- **`match_filter()`** — Evaluates whether a URL matches an include or exclude filter, using either regex or case-insensitive substring matching.
- **`decode_file_id()`** — Extracts the file path from a full URL (strips scheme + domain), URL-decodes it, and cleans up encoding issues.
- **`sanitize_path()`** — On Windows, replaces characters not allowed in file paths (`:*?&=<>|`) with percent-encoded equivalents.
- **`tidy_bytes()`** — Handles strings with potential encoding issues by round-tripping through UTF-8 with error replacement.

## Wayback Machine CDX API Reference

The CDX (Capture/Crawl inDeX) API is a free, public, no-auth-required API provided by the Internet Archive. It returns an index of all URLs archived under a given domain.

**Base URL:** `https://web.archive.org/cdx/search/cdx`

### Query Parameters

| Parameter | Description | Example |
|---|---|---|
| `url` | Target URL. Append `/*` for all files under a domain | `www.example.com/*` |
| `output` | Response format (`json`, `text`, `csv`) | `json` |
| `fl` | Fields to return (comma-separated) | `timestamp,original,mimetype,length` |
| `from` | Start timestamp (YYYYMMDDhhmmss or prefix like YYYY) | `20170101` |
| `to` | End timestamp | `20171231` |
| `collapse` | Deduplicate by field (e.g., `digest` removes identical content) | `digest` |
| `filter` | Filter results by field value | `statuscode:200` |
| `matchType` | How to match the URL (`exact`, `prefix`, `host`, `domain`) | `prefix` |
| `page` | Page index for paginated results | `0` |
| `limit` | Max results to return | `50` |

### Available Fields (`fl` parameter)

`urlkey`, `timestamp`, `original`, `mimetype`, `statuscode`, `digest`, `length`

### How This Tool Uses the API

The tool makes two types of CDX queries per download:

1. **Exact URL query** — `url=example.com` (no wildcard) to get the root page
2. **Wildcard query** — `url=example.com/*` with pagination to get all files under the domain

Parameters sent by this tool: `output=json`, `fl=timestamp,original,mimetype,length`, `collapse=digest`, `gzip=false`, `filter=statuscode:200` (unless `--all` flag), plus optional `from`/`to` timestamp bounds and `page` index.

### Downloading Original Files

After getting the file list from CDX, files are downloaded from:

```
https://web.archive.org/web/{timestamp}id_/{original_url}
```

The `id_` suffix is critical — it tells the Wayback Machine to return the **original file** without injecting its toolbar, rewriting URLs, or modifying content in any way.

### Rate Limiting

The API is free and requires no authentication. However, the Internet Archive applies rate limiting. Heavy concurrent requests can result in temporary IP bans. This tool uses tenacity for automatic retry with exponential backoff on 429 and 5xx responses.

### Further Reading

- [Official CDX API docs](https://archive.org/developers/wayback-cdx-server.html)
- [CDX Server GitHub](https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server)
- [Wayback Machine APIs overview](https://archive.org/help/wayback_api.php)

See [CDX_API_EXAMPLES.md](CDX_API_EXAMPLES.md) for working curl examples using `www.touchcommerce.com`.
