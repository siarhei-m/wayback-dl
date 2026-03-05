# Planned Work

Roadmap for future wayback-dl improvements.

## v1.1 — Robustness & Safety

- [ ] **Rate limiting** (`--wait SECONDS`, `--random-wait`)
  Configurable delay between downloads to avoid IP bans from archive.org.
  `--random-wait` adds jitter (0.5x to 1.5x of wait value), similar to wget.

- [ ] **Max file size** (`--max-file-size SIZE`)
  Skip files larger than the specified size (e.g., `--max-file-size 50MB`).
  Prevents surprise multi-GB downloads of archived videos or disk images.
  Uses the CDX `length` field to skip before downloading.

- [ ] **Disk space pre-flight check**
  Before downloading, estimate total size from CDX data and compare with
  available disk space. Warn if estimated size exceeds 80% of free space.
  Show confirmation prompt with total size.

- [ ] **Log to file** (`--log FILE`)
  Write download logs to a file in addition to stderr. Useful for long-running
  downloads and debugging after the fact. Include timestamps, file paths,
  HTTP status codes, and sizes.

- [ ] **Better "no results" diagnostics**
  When no files are found, check if the URL exists in Wayback Machine at all
  (query CDX without filters). Distinguish between "URL never archived" vs
  "no files match your filters/date range". Suggest removing filters or
  widening the date range.

## v1.2 — Power User Features

- [ ] **Post-download link rewriting**
  After downloading, rewrite absolute URLs in HTML and CSS files to point to
  local relative paths so the downloaded site works offline. Handles:
  - `<a href>`, `<img src>`, `<link href>`, `<script src>` in HTML
  - `url()` references in CSS
  - Enabled by default, disable with `--no-rewrite`.

- [ ] **Config file support**
  Load default settings from `~/.wayback_dl/config.toml` or a project-local
  `wayback-dl.toml`. Allows setting default concurrency, output directory,
  rate limits, etc. CLI flags override config file values.

- [ ] **Content-type filter presets**
  Built-in filter shortcuts instead of crafting regexes:
  - `--only-html` — download only HTML pages
  - `--only-media` — download only images, audio, video
  - `--no-images` — download everything except images
  - `--no-media` — download everything except images, audio, video
  Can be combined with `--only` and `--exclude` for further refinement.

- [ ] **Batch mode** (`--batch FILE`)
  Download multiple sites listed in a text file (one URL per line) or a YAML
  config with per-site settings. Track progress across all sites.
  ```yaml
  sites:
    - url: http://example.com
      from: 2017-01-01
      to: 2017-12-31
    - url: http://other.com
      concurrency: 10
  ```

- [ ] **User-Agent header** (`--user-agent STRING`)
  Customize the User-Agent header sent to archive.org. Some archived responses
  vary by user agent. Default should identify as wayback-dl with version.

## v1.3+ — Advanced Features

- [ ] **Session cleanup** (`--clean-sessions`)
  Remove old interrupted sessions. Options: `--clean-sessions` removes all,
  `--clean-sessions 30d` removes sessions older than 30 days.

- [ ] **Sitemap generation** (`--sitemap`)
  Generate a `sitemap.xml` from the downloaded file structure. Useful for
  re-hosting the archived site or analyzing its structure.

- [ ] **Download report**
  At the end of a download, print a summary showing:
  - Total files downloaded / skipped / failed
  - Total size downloaded
  - Failed files list with error reasons
  - Files that were truncated or empty
  Save report to `download-report.json` in the output directory.

- [ ] **Subdomain support** (`--include-subdomains`)
  Also download related subdomains (e.g., `cdn.example.com`,
  `static.example.com`). Uses the CDX API `matchType=domain` parameter
  to discover all subdomains.

- [ ] **Snapshot sampling** (`--interval PERIOD`)
  Instead of downloading only the latest or all snapshots, sample at regular
  intervals: `--interval monthly`, `--interval weekly`, `--interval yearly`.
  Downloads one snapshot per period per file, useful for tracking site
  evolution over time without downloading every snapshot.
