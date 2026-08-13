"""Landing / discovery chrome branding helpers."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from config import settings

_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


_DEFAULT_PRIMARY = "#013366"


_DEFAULT_SECONDARY = "#FCBA19"


_DEFAULT_LOGO = (
    "https://mines.nrs.gov.bc.ca/assets/images/bcgov-mineinfo-horiz-LG.png"
)


def safe_css_color(value: str | None, *, default: str) -> str:
    """Allow only ``#RGB`` / ``#RRGGBB`` / ``#RRGGBBAA`` for CSS interpolation."""
    raw = (value or "").strip()
    if _HEX_COLOR_RE.fullmatch(raw):
        return raw
    return default


def safe_http_url(value: str | None) -> str:
    """Allow ``http`` / ``https`` absolute URLs only; otherwise empty."""
    raw = (value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return raw
    return ""


def safe_asset_url(value: str | None, *, default: str) -> str:
    """Allow http(s) URLs or same-origin paths (``/…``, not ``//…``)."""
    raw = (value or "").strip()
    if not raw:
        return default
    if raw.startswith("/") and not raw.startswith("//"):
        return raw
    if safe_http_url(raw):
        return raw
    return default


def _branding() -> dict[str, Any]:
    return {
        "project_title": settings.PROJECT_TITLE,
        "project_version": settings.PROJECT_VERSION,
        "tagline": settings.LANDING_TAGLINE,
        "description": (settings.LANDING_DESCRIPTION or "").strip(),
        "logo_url": safe_asset_url(
            settings.LANDING_LOGO_URL, default=_DEFAULT_LOGO
        ),
        "primary_color": safe_css_color(
            settings.LANDING_PRIMARY_COLOR, default=_DEFAULT_PRIMARY
        ),
        "secondary_color": safe_css_color(
            settings.LANDING_SECONDARY_COLOR, default=_DEFAULT_SECONDARY
        ),
        "partner_url": safe_http_url(settings.LANDING_PARTNER_URL),
        "partner_label": (settings.LANDING_PARTNER_LABEL or "Partner").strip()
        or "Partner",
    }
