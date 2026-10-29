"""Ad previews: Meta's iframe HTML is never echoed; only a validated URL is kept.

``GET /{ad_id}/previews`` returns ``data[].body`` with an ``<iframe src=...>`` pointing at
``https://www.facebook.com/ads/api/preview_iframe.php`` or, for ads of a business portfolio,
``https://business.facebook.com/…`` (valid 24 h). ``from_graph_body``
extracts that ``src`` and accepts it only for that exact scheme, host and path; the studio
renders its own iframe with it (CSP ``frame-src`` lists both hosts).
"""

from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

from studio.meta.adapter import AdapterError, Preview

HOSTS = frozenset({"www.facebook.com", "business.facebook.com"})
PATH = "/ads/api/preview_iframe.php"
_SRC = re.compile(r"""<iframe\b[^>]*\bsrc\s*=\s*(["'])(.*?)\1""", re.IGNORECASE | re.S)


def from_graph_body(body: str) -> Preview:
    m = _SRC.search(body or "")
    if m is None:
        raise AdapterError("preview has no iframe", code="bad_preview")
    src = html.unescape(m.group(2)).strip()
    parts = urlsplit(src)
    if (parts.scheme != "https" or parts.hostname not in HOSTS or parts.path != PATH
            or parts.username or parts.password or parts.port not in (None, 443)
            or any(c in src for c in "\"'<> \\")):
        raise AdapterError("preview URL is not a Meta preview iframe", code="bad_preview")
    return Preview("iframe", src=src)
