"""CDX API client for querying the Wayback Machine snapshot index.

The CDX (Capture/Crawl inDeX) API is a free, public API provided by the
Internet Archive. It returns an index of all URLs archived under a given
domain — HTML pages, JS, CSS, images, fonts, etc. — without needing to
parse any HTML or crawl pages.

API docs: https://archive.org/developers/wayback-cdx-server.html
"""

import json
import logging
import time
from urllib.parse import urlencode

import aiohttp
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

logger = logging.getLogger(__name__)

# Official CDX API endpoint (not the undocumented /xd variant)
CDX_API_URL = "https://web.archive.org/cdx/search/cdx"

# Fields requested from the CDX API
CDX_FIELDS = "timestamp,original,mimetype,length"
CDX_HEADER = ["timestamp", "original", "mimetype", "length"]


class CDXError(Exception):
    """Retryable error from the CDX API (e.g., rate limiting, server errors)."""


def _build_params(
    url: str,
    page_index: int | None = None,
    include_all: bool = False,
    from_timestamp: int = 0,
    to_timestamp: int = 0,
) -> list[tuple[str, str]]:
    """Build query parameters for a CDX API request.

    Args:
        url: The URL to search for (supports wildcards like 'example.com/*').
        page_index: Page number for paginated results. None for unpaginated.
        include_all: If False, filter to statuscode:200 only.
        from_timestamp: Include only snapshots on or after this timestamp.
        to_timestamp: Include only snapshots on or before this timestamp.
    """
    params: list[tuple[str, str]] = [
        ("output", "json"),
        ("url", url),
        ("fl", CDX_FIELDS),
        ("collapse", "digest"),
        ("gzip", "false"),
    ]

    # By default, only include successful responses (200 OK)
    if not include_all:
        params.append(("filter", "statuscode:200"))

    # Timestamp bounds for narrowing the time range
    if from_timestamp:
        params.append(("from", str(from_timestamp)))
    if to_timestamp:
        params.append(("to", str(to_timestamp)))

    # Pagination: the CDX API returns ~150k results per page
    if page_index is not None:
        params.append(("page", str(page_index)))

    return params


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type((aiohttp.ClientError, CDXError, TimeoutError)),
    before_sleep=lambda retry_state: logger.warning(
        "CDX API request failed, retrying (attempt %d)...", retry_state.attempt_number
    ),
)
async def _fetch_snapshot_page(
    session: aiohttp.ClientSession,
    url: str,
    page_index: int | None = None,
    include_all: bool = False,
    from_timestamp: int = 0,
    to_timestamp: int = 0,
) -> list[list[str]]:
    """Fetch a single page of snapshots from the CDX API.

    Returns a list of [timestamp, original_url, mimetype, length] entries.
    Retries up to 5 times with exponential backoff on transient errors.
    """
    params = _build_params(url, page_index, include_all, from_timestamp, to_timestamp)

    # Log before the request starts, so the user sees activity even if
    # the request hangs. Short message at INFO, full URL at DEBUG.
    full_url = f"{CDX_API_URL}?{urlencode(params)}"
    page_label = f"page {page_index}" if page_index is not None else "exact URL"
    logger.info("Querying CDX API (%s)...", page_label)
    logger.debug("  GET %s", full_url)

    start = time.monotonic()

    async with session.get(
        CDX_API_URL, params=params, timeout=aiohttp.ClientTimeout(total=30)
    ) as resp:
        elapsed = time.monotonic() - start

        # 429 and 5xx are retryable errors (raised as CDXError for tenacity)
        if resp.status == 429:
            logger.warning("CDX API: 429 rate limited (%.1fs)", elapsed)
            raise CDXError("Rate limited by CDX API (429)")
        if resp.status >= 500:
            logger.warning("CDX API: %d server error (%.1fs)", resp.status, elapsed)
            raise CDXError(f"CDX API server error ({resp.status})")
        if resp.status != 200:
            logger.info("CDX API: status %d (%.1fs)", resp.status, elapsed)
            return []

        text = await resp.text()
        if not text.strip():
            logger.info("CDX API: empty response (%.1fs)", elapsed)
            return []

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            logger.warning("CDX API: invalid JSON (%d bytes, %.1fs)", len(text), elapsed)
            return []

        # Strip header row if present
        if data and data[0] == CDX_HEADER:
            data = data[1:]

        logger.info(
            "CDX API: %d snapshots, %d bytes, HTTP %d (%.1fs)",
            len(data), len(text), resp.status, elapsed,
        )
        return data


async def get_all_snapshots(
    session: aiohttp.ClientSession,
    base_url: str,
    exact_url: bool = False,
    maximum_pages: int = 100,
    include_all: bool = False,
    from_timestamp: int = 0,
    to_timestamp: int = 0,
    progress_callback=None,
) -> list[list[str]]:
    """Fetch all snapshots for a URL from the CDX API.

    This performs two phases of CDX queries:
    1. Exact URL query — fetches snapshots for the root URL itself
    2. Wildcard query — fetches snapshots for all URLs under the domain
       (url/*) with pagination, unless exact_url=True

    Returns a list of [timestamp, original_url, mimetype, length] entries.
    """
    snapshots: list[list[str]] = []

    if progress_callback:
        progress_callback("Fetching snapshot index from CDX API...")

    # Phase 1: Fetch snapshots for the exact URL (root page)
    exact_results = await _fetch_snapshot_page(
        session, base_url,
        include_all=include_all,
        from_timestamp=from_timestamp,
        to_timestamp=to_timestamp,
    )
    snapshots.extend(exact_results)

    if not exact_url:
        # Phase 2: Fetch all URLs under the domain using wildcard + pagination
        wildcard_url = base_url.rstrip('/') + '/*'
        for page_index in range(maximum_pages):
            page_results = await _fetch_snapshot_page(
                session, wildcard_url,
                page_index=page_index,
                include_all=include_all,
                from_timestamp=from_timestamp,
                to_timestamp=to_timestamp,
            )
            # Empty page means we've exhausted all results
            if not page_results:
                break
            snapshots.extend(page_results)
            if progress_callback:
                progress_callback(f"  Page {page_index + 1}: {len(snapshots)} snapshots so far")

    if progress_callback:
        progress_callback(f"Found {len(snapshots)} snapshots to consider.")

    return snapshots
