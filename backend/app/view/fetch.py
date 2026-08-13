"""Outbound /view fetch helpers, URL parsing, and SSRF guards."""

from __future__ import annotations

import ipaddress
import socket
from typing import Any, Literal

import requests
from urllib.parse import urlparse

from app.services.composer import publisher_origin
from app.plugins.mongodb import MongoClient
from config import settings

ViewFetchKind = Literal["credential", "status", "oca"]

_VIEW_FETCH_TIMEOUT_S = 30


def _parse_path_id_url(
    url: str,
    *,
    path_prefix: str,
    require_same_origin: bool,
) -> str | None:
    """Extract a single path segment after ``path_prefix`` from an http(s) URL."""
    raw = (url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    if require_same_origin:
        origin = urlparse(publisher_origin())
        if (
            parsed.scheme != origin.scheme
            or parsed.netloc.lower() != (origin.netloc or "").lower()
        ):
            return None
    path = (parsed.path or "").rstrip("/")
    if not path.startswith(path_prefix):
        return None
    value = path[len(path_prefix) :]
    if not value or "/" in value:
        return None
    return value


def parse_same_origin_credential_url(url: str) -> str | None:
    """Return credential id if ``url`` is this publisher's ``/credentials/{id}``."""
    return _parse_path_id_url(
        url,
        path_prefix="/credentials/",
        require_same_origin=True,
    )


def parse_credential_url(url: str, *, allow_remote: bool | None = None) -> str | None:
    """Return credential id from a ``/credentials/{id}`` URL.

    Same-origin only unless ``allow_remote`` / ``VIEW_UNSAFE_MODE`` is enabled.
    """
    remote = settings.VIEW_UNSAFE_MODE if allow_remote is None else allow_remote
    return _parse_path_id_url(
        url,
        path_prefix="/credentials/",
        require_same_origin=not remote,
    )


def view_allows_remote() -> bool:
    """True when ``/view`` may fetch off-origin credential / status / OCA URLs."""
    return bool(settings.VIEW_UNSAFE_MODE)


def is_http_url(url: str) -> bool:
    """True for absolute ``http`` / ``https`` URLs with a host."""
    parsed = urlparse((url or "").strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


class ViewFetchError(ValueError):
    """URL rejected by the /view outbound fetch allowlist / SSRF checks."""


def ip_is_blocked_for_view_fetch(address: str) -> bool:
    """True when ``address`` is not a public unicast IP (SSRF denylist)."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    # ``is_global`` excludes loopback, private, link-local, multicast, reserved, etc.
    return not ip.is_global


def resolve_view_fetch_host_ips(hostname: str) -> list[str]:
    """Resolve ``hostname`` (or IP literal) to address strings for SSRF checks."""
    host = (hostname or "").strip().strip("[]")
    if not host:
        return []
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ViewFetchError(f"Could not resolve host {host!r}") from exc
    addresses: list[str] = []
    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        addr = str(sockaddr[0])
        if addr not in seen:
            seen.add(addr)
            addresses.append(addr)
    return addresses


def view_fetch_url_is_publisher(url: str) -> bool:
    """True when ``url`` matches this publisher's scheme + netloc."""
    parsed = urlparse((url or "").strip())
    origin = urlparse(publisher_origin())
    return (
        parsed.scheme == origin.scheme
        and bool(parsed.netloc)
        and parsed.netloc.lower() == (origin.netloc or "").lower()
    )


def assert_view_fetch_host_allowed(url: str) -> None:
    """Block remote fetches whose DNS (or literal IP) is not public unicast.

    Same-origin publisher URLs skip the check so local demo hosts
    (``127.0.0.1``, etc.) keep working.
    """
    if view_fetch_url_is_publisher(url):
        return
    parsed = urlparse((url or "").strip())
    hostname = parsed.hostname
    if not hostname:
        raise ViewFetchError("URL is missing a host")
    addresses = resolve_view_fetch_host_ips(hostname)
    if not addresses:
        raise ViewFetchError(f"Could not resolve host {hostname!r}")
    blocked = [addr for addr in addresses if ip_is_blocked_for_view_fetch(addr)]
    if blocked:
        raise ViewFetchError(
            f"Refusing to fetch non-public address for host {hostname!r}"
        )


def validate_view_fetch_url(url: str, *, kind: ViewFetchKind) -> str:
    """Validate ``url`` shape, path allowlist, and origin policy for ``kind``.

    Returns the stripped URL. Does not perform DNS / IP checks (see
    :func:`assert_view_fetch_host_allowed`).
    """
    raw = (url or "").strip()
    if not raw:
        raise ViewFetchError("URL is empty")
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ViewFetchError("URL must be absolute http(s) with a host")
    if parsed.username is not None or parsed.password is not None:
        raise ViewFetchError("URL must not include userinfo")

    remote = view_allows_remote()
    if kind == "credential":
        if parse_credential_url(raw, allow_remote=remote) is None:
            raise ViewFetchError(
                "credential URL must be "
                + (
                    "an http(s) /credentials/{id} URL"
                    if remote
                    else "a same-origin /credentials/{id} URL"
                )
            )
    elif kind == "status":
        if parse_status_list_url(raw, allow_remote=remote) is None:
            raise ViewFetchError(
                "status-list URL must be "
                + (
                    "an http(s) /status-lists/{id} URL"
                    if remote
                    else "a same-origin /status-lists/{id} URL"
                )
            )
    elif kind == "oca":
        if parse_oca_url(raw, allow_remote=remote) is None:
            raise ViewFetchError(
                "OCA URL must be "
                + (
                    "an http(s) /templates/{type}/{version}/oca.json URL"
                    if remote
                    else "a same-origin /templates/{type}/{version}/oca.json URL"
                )
            )
    else:
        raise ViewFetchError(f"Unknown view fetch kind {kind!r}")
    return raw


def safe_view_get(
    url: str,
    *,
    kind: ViewFetchKind,
    accept: str,
) -> requests.Response:
    """GET ``url`` after path/origin/SSRF checks; redirects are refused."""
    target = validate_view_fetch_url(url, kind=kind)
    assert_view_fetch_host_allowed(target)
    response = requests.get(
        target,
        headers={"Accept": accept},
        timeout=_VIEW_FETCH_TIMEOUT_S,
        allow_redirects=False,
    )
    if 300 <= response.status_code < 400:
        raise ViewFetchError(
            f"Refusing HTTP redirect from view fetch ({response.status_code})"
        )
    return response


def parse_same_origin_oca_url(url: str) -> tuple[str, str] | None:
    """Return ``(credential_type, version)`` for same-origin ``/templates/.../oca.json``."""
    return parse_oca_url(url, allow_remote=False)


def parse_oca_url(
    url: str, *, allow_remote: bool | None = None
) -> tuple[str, str] | None:
    """Return ``(credential_type, version)`` for ``/templates/{type}/{version}/oca.json``.

    Same-origin only unless ``allow_remote`` / ``VIEW_UNSAFE_MODE`` is enabled.
    """
    raw = (url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    remote = settings.VIEW_UNSAFE_MODE if allow_remote is None else allow_remote
    if not remote:
        origin = urlparse(publisher_origin())
        if (
            parsed.scheme != origin.scheme
            or parsed.netloc.lower() != (origin.netloc or "").lower()
        ):
            return None
    return parse_oca_templates_path(raw)


def parse_oca_templates_path(url: str) -> tuple[str, str] | None:
    """Return ``(credential_type, version)`` from a ``/templates/.../oca.json`` URL path."""
    raw = (url or "").strip()
    if not raw:
        return None
    path = (urlparse(raw).path or "").rstrip("/")
    prefix = "/templates/"
    if not path.startswith(prefix) or not path.endswith("/oca.json"):
        return None
    rest = path[len(prefix) : -len("/oca.json")]
    parts = [p for p in rest.split("/") if p]
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def fetch_oca_json(url: str) -> dict[str, Any]:
    """GET an OCA bundle JSON document from ``url``."""
    try:
        response = safe_view_get(url, kind="oca", accept="application/json")
    except ViewFetchError:
        raise
    if response.status_code == 404:
        raise LookupError("OCA bundle not found")
    if not response.ok:
        raise RuntimeError(f"OCA fetch failed with HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("OCA response was not JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("OCA response was not a JSON object")
    return payload


def parse_same_origin_status_list_url(url: str) -> str | None:
    """Return status-list id if ``url`` is this publisher's ``/status-lists/{id}``."""
    return parse_status_list_url(url, allow_remote=False)


def parse_status_list_url(url: str, *, allow_remote: bool | None = None) -> str | None:
    """Return status-list id from a ``/status-lists/{id}`` URL.

    Same-origin only unless ``allow_remote`` / ``VIEW_UNSAFE_MODE`` is enabled.
    """
    return _parse_path_id_url(
        url,
        path_prefix="/status-lists/",
        require_same_origin=not (
            settings.VIEW_UNSAFE_MODE if allow_remote is None else allow_remote
        ),
    )


def fetch_application_vc(
    url: str,
    *,
    kind: Literal["credential", "status"] = "credential",
) -> dict[str, Any]:
    """GET ``url`` with ``Accept: application/vc`` and return the JSON body."""
    try:
        response = safe_view_get(url, kind=kind, accept="application/vc")
    except ViewFetchError:
        raise
    if response.status_code == 404:
        raise LookupError("Credential not found")
    if not response.ok:
        raise RuntimeError(
            f"Credential fetch failed with HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Credential response was not JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Credential response was not a JSON object")
    return payload


def load_publisher_enveloped_vc(credential_id: str) -> dict[str, Any] | None:
    """Return an enveloped VC from Mongo when ``credential_id`` is published here.

    Avoids HTTP self-fetch to ``PUBLISHER_DOMAIN`` (e.g. ``localhost`` vs
    ``127.0.0.1`` hang/timeout when the viewer re-requests the credential).
    """
    cid = (credential_id or "").strip()
    if not cid:
        return None
    from app.plugins.traction import TractionController

    try:
        record = MongoClient().find_one("CredentialRecord", {"id": cid})
    except Exception:
        settings.LOGGER.exception(
            "View: Mongo load failed for credential %s", cid
        )
        return None
    if not isinstance(record, dict):
        return None
    vc_jwt = str(record.get("vc_jwt") or "").strip()
    if not vc_jwt:
        return None
    return TractionController.as_enveloped_vc(vc_jwt)


def resolve_application_vc(url: str) -> dict[str, Any]:
    """Load a credential envelope via Mongo when local, otherwise HTTP fetch."""
    credential_id = parse_credential_url(url)
    if credential_id and view_fetch_url_is_publisher(url):
        local = load_publisher_enveloped_vc(credential_id)
        if local is not None:
            return local
    return fetch_application_vc(url)


def resolve_internal_oca_bundle(url: str) -> dict[str, Any] | None:
    """Load OCA from repo configs when ``url`` is a ``/templates/{type}/{ver}/oca.json`` path.

    Ignores host so published credentials that point at this publisher's template
    URL can be rendered from the local bundle (no HTTP self-fetch).
    """
    from app.view.oca import oca_bundle_for_credential_type

    parsed = parse_oca_templates_path(url)
    if not parsed:
        return None
    cred_type, _version = parsed
    return oca_bundle_for_credential_type(cred_type)

def load_status_list_credential(url: str) -> dict[str, Any]:
    """Fetch a status-list credential URL and return the unwrapped VC document."""
    from app.view.checks import (
        _ENVELOPED_VC_TYPE,
        _VC_JWT_DATA_PREFIX,
        _as_string_list,
        decode_jwt_payload,
        extract_vc_jwt,
    )

    document = fetch_application_vc(url, kind="status")
    types = _as_string_list(document.get("type"))
    if _ENVELOPED_VC_TYPE in types or str(document.get("id") or "").startswith(
        _VC_JWT_DATA_PREFIX
    ):
        token = extract_vc_jwt(document)
        return decode_jwt_payload(token)
    return document
