from __future__ import annotations

import os
import re
import time
from datetime import datetime
from typing import Any, Callable, Optional, Sequence, TypeVar

from playwright.sync_api import Locator

T = TypeVar("T")


def now_ts() -> str:
    """Timestamp suitable for filenames."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_dir(path: str) -> str:
    """Create directory if it doesn't exist and return the path."""
    os.makedirs(path, exist_ok=True)
    return path


def sanitize_filename(name: str, max_len: int = 180) -> str:
    """Make filename safe for most filesystems."""
    name = (name or "").strip()
    # Replace illegal filename chars
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name)
    # Collapse whitespace
    name = re.sub(r"\s+", " ", name)

    if len(name) <= max_len:
        return name

    root, ext = os.path.splitext(name)
    keep = max_len - len(ext) - 1
    if keep <= 0:
        return name[:max_len]
    return root[:keep] + "_" + ext


def sleep_ms(ms: int) -> None:
    time.sleep(ms / 1000)


def first_match_locator(
    scope: Any,
    selectors: Sequence[str],
    *,
    must_be_visible: bool = False,
    timeout_ms_each: int = 300,
) -> Optional[Locator]:
    """Try a list of selectors and return the first matching locator.

    `scope` can be a Playwright `Page` or `Locator` (anything with `.locator()`).
    """
    for sel in selectors:
        try:
            loc = scope.locator(sel).first
            if must_be_visible:
                loc.wait_for(state="visible", timeout=timeout_ms_each)
                return loc
            if loc.count() > 0:
                return loc
        except Exception:
            continue
    return None


def first_match_role_locator(
    scope: Any,
    *,
    role: str,
    name_patterns: Sequence[str],
    must_be_visible: bool = False,
) -> Optional[Locator]:
    """Try role+name patterns and return the first matching locator."""
    for pat in name_patterns:
        try:
            loc = scope.get_by_role(role, name=re.compile(pat, re.I)).first
            if must_be_visible:
                loc.wait_for(state="visible", timeout=300)
                return loc
            if loc.count() > 0:
                return loc
        except Exception:
            continue
    return None


def normalize_cdp_endpoint(endpoint: str) -> str:
    """Normalize CDP endpoint to http://host:port."""
    e = (endpoint or "").strip()
    if not e:
        return "http://127.0.0.1:9222"

    m = re.match(r"^wss?://([^/]+)", e)
    if m:
        return f"http://{m.group(1)}"
    return e


def safe_inner_text(locator: Locator) -> str:
    """Best-effort text extraction."""
    try:
        return (locator.inner_text(timeout=1000) or "").strip()
    except Exception:
        try:
            txt = locator.text_content(timeout=1000)  # type: ignore[arg-type]
            return (txt or "").strip()
        except Exception:
            return ""


def safe_inner_html(locator: Locator) -> str:
    """Best-effort HTML extraction (preserves formatting)."""
    try:
        return (locator.inner_html(timeout=1000) or "").strip()
    except Exception:
        return ""


def html_to_markdown(html: str) -> str:
    """Convert HTML to Markdown (best-effort, preserves basic formatting).
    
    This is a simple converter that handles common HTML tags.
    For more complex cases, consider using markdownify or html2text.
    """
    if not html:
        return ""
    
    import re
    
    # Remove script and style tags
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Headers
    html = re.sub(r'<h1[^>]*>(.*?)</h1>', r'# \1\n\n', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<h2[^>]*>(.*?)</h2>', r'## \1\n\n', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<h3[^>]*>(.*?)</h3>', r'### \1\n\n', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<h4[^>]*>(.*?)</h4>', r'#### \1\n\n', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<h5[^>]*>(.*?)</h5>', r'##### \1\n\n', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<h6[^>]*>(.*?)</h6>', r'###### \1\n\n', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Bold and italic
    html = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Code blocks
    html = re.sub(r'<pre[^>]*><code[^>]*>(.*?)</code></pre>', r'```\n\1\n```', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Links
    html = re.sub(r'<a[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', r'[\2](\1)', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Lists
    html = re.sub(r'<ul[^>]*>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'</ul>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<ol[^>]*>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'</ol>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1\n', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Paragraphs and line breaks
    html = re.sub(r'<p[^>]*>(.*?)</p>', r'\1\n\n', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<br[^>]*>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<div[^>]*>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'</div>', '\n', html, flags=re.IGNORECASE)
    
    # Remove remaining HTML tags
    html = re.sub(r'<[^>]+>', '', html)
    
    # Clean up whitespace
    html = re.sub(r'\n{3,}', '\n\n', html)
    html = html.strip()
    
    return html


def with_retry(
    fn: Callable[[], T],
    *,
    retries: int = 3,
    backoff_ms: int = 500,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    """Retry helper with exponential backoff."""
    last_err: Optional[BaseException] = None
    for i in range(retries):
        try:
            return fn()
        except retry_on as e:
            last_err = e
            if i < retries - 1:
                sleep_ms(backoff_ms * (2**i))
    assert last_err is not None
    raise last_err
