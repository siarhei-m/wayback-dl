"""Utility functions for URL filtering, path handling, and encoding cleanup.

These utilities support the core downloader by:
- Parsing user-supplied filter strings into regex patterns or substring matches
- Extracting and decoding file paths from archived URLs
- Sanitizing file paths for cross-platform compatibility and security
"""

import re
import platform
from urllib.parse import unquote


# Maximum length for user-supplied regex patterns to prevent ReDoS
MAX_REGEX_LENGTH = 500


def to_regex(filter_str: str) -> re.Pattern | None:
    """Parse a filter string into a compiled regex pattern.

    Supports /pattern/flags notation, e.g.:
        '/\\.jpg$/i'  -> re.compile('\\.jpg$', re.IGNORECASE)
        '/\\.(gif|jpg|jpeg)$/i' -> matches image extensions case-insensitively

    Returns None for plain strings or empty input, signaling the caller
    to use case-insensitive substring matching instead.

    Supported flags: i (IGNORECASE), m (MULTILINE), x (VERBOSE), s (DOTALL).

    Raises ValueError if the regex pattern exceeds MAX_REGEX_LENGTH or is invalid.
    """
    if not filter_str:
        return None

    # Match /pattern/flags format
    match = re.match(r'^/(.*)/([imxs]*)$', filter_str)
    if not match:
        return None

    pattern, flags_str = match.groups()

    # Guard against ReDoS: reject overly long patterns
    if len(pattern) > MAX_REGEX_LENGTH:
        raise ValueError(
            f"Regex pattern too long ({len(pattern)} chars, max {MAX_REGEX_LENGTH}). "
            "Use a shorter pattern or a plain substring filter."
        )

    flags = 0
    if 'i' in flags_str:
        flags |= re.IGNORECASE
    if 'm' in flags_str:
        flags |= re.MULTILINE
    if 'x' in flags_str:
        flags |= re.VERBOSE
    if 's' in flags_str:
        flags |= re.DOTALL

    try:
        return re.compile(pattern, flags)
    except re.error as e:
        raise ValueError(f"Invalid regex pattern: {e}") from e


def match_filter(file_url: str, filter_str: str | None, *, exclude: bool = False) -> bool:
    """Check if a URL matches a filter string.

    Behavior depends on the filter type and the exclude flag:

    For include filters (exclude=False, used with --only):
        - No filter set -> True (include everything)
        - Filter matches -> True
        - Filter doesn't match -> False

    For exclude filters (exclude=True, used with --exclude):
        - No filter set -> False (exclude nothing)
        - Filter matches -> True (this URL should be excluded)
        - Filter doesn't match -> False

    The filter can be a /regex/ pattern or a plain substring (case-insensitive).
    """
    if not filter_str:
        return not exclude

    regex = to_regex(filter_str)
    if regex:
        # Regex filter: search anywhere in the URL
        matched = regex.search(file_url) is not None
    else:
        # Plain string filter: case-insensitive substring match
        matched = filter_str.lower() in file_url.lower()

    return matched if exclude else matched


def sanitize_path(path: str) -> str:
    """Sanitize a file path for the current platform.

    On all platforms:
    - Strips null bytes and control characters (0x00-0x1f, 0x7f)
    - Removes path traversal components (.. and .)

    On Windows, additionally replaces characters not allowed in file paths
    (e.g., ':' -> '%3a').
    """
    # Strip null bytes and control characters on all platforms
    path = re.sub(r'[\x00-\x1f\x7f]', '', path)

    # Remove path traversal components
    parts = path.split('/')
    safe_parts = [p for p in parts if p not in ('..', '.')]
    path = '/'.join(safe_parts)

    if platform.system() == 'Windows':
        path = re.sub(r'[:*?&=<>\\|]', lambda m: f'%{ord(m.group()):02x}', path)
    return path


def tidy_bytes(s: str) -> str:
    """Clean up a string that may contain encoding issues.

    Python strings are always valid Unicode internally, so this mainly
    serves as a safety net for strings decoded from URLs that may have
    been in CP-1252 or ISO-8859-1 originally. Round-trips through UTF-8
    encoding to verify validity, falling back to replacement characters.
    """
    try:
        return s.encode('utf-8').decode('utf-8')
    except (UnicodeDecodeError, UnicodeEncodeError):
        return s.encode('utf-8', errors='replace').decode('utf-8')


def decode_file_id(file_url: str) -> str | None:
    """Extract and decode the file ID (relative path) from an archived URL.

    The CDX API returns full URLs like:
        'http://www.example.com:80/path/file.html'

    This function extracts just the path portion after the domain:
        'path/file.html'

    The result is URL-decoded, encoding-cleaned, and sanitized against
    path traversal. Returns None if the URL is malformed (no slashes).
    """
    if '/' not in file_url:
        return None

    # Split on '/' and take everything after the 3rd slash
    # e.g., ['http:', '', 'www.example.com:80', 'path', 'file.html']
    #         [0]     [1]  [2]                   [3:]
    parts = file_url.split('/')
    file_id = '/'.join(parts[3:])
    file_id = unquote(file_id)  # URL-decode percent-encoded characters
    file_id = tidy_bytes(file_id) if file_id else file_id

    # Remove path traversal components (.. and .) after decoding
    # to prevent %2e%2e%2f from bypassing the check
    if file_id:
        safe_parts = [p for p in file_id.split('/') if p not in ('..', '.')]
        file_id = '/'.join(safe_parts)

    return file_id


def validate_url(url: str) -> str:
    """Validate that a URL has a supported scheme.

    Only http and https URLs are allowed. Bare domains (no scheme) are
    also accepted as they'll be passed to the CDX API as-is.

    Returns the URL unchanged if valid, raises ValueError otherwise.
    """
    # Check for scheme (both :// and : without //)
    if ':' in url.split('/')[0]:
        scheme = url.split(':')[0].lower()
        if scheme not in ('http', 'https'):
            raise ValueError(
                f"Unsupported URL scheme: '{scheme}'. Only http and https are supported."
            )
    return url
