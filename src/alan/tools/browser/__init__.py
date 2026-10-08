"""Browser capability for Alan.

Only the client lives here. The browser itself runs in an isolated sidecar
environment so that ``browser-use``'s exact pins cannot conflict with Alan's
main environment (ADR-003).
"""

from .sidecar import (
    DEFAULT_BASE_URL,
    BrowserUnavailable,
    SidecarBrowser,
    build_browser_provider,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "BrowserUnavailable",
    "SidecarBrowser",
    "build_browser_provider",
]
