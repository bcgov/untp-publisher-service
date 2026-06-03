"""BC Laws CiviX API client (Open Legislation).

See https://www.bclaws.gov.bc.ca/bclawsapi.html and the Content API docs at
https://www.bclaws.gov.bc.ca/civix/template/complete/api/API_content.html
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urljoin

import requests
from fastapi import HTTPException

from app.plugins.soup import Soup
from config import settings

_ASPECT = "complete"
_INDEX = "statreg"
_LETTER_RE = re.compile(r"^--\s*([A-Z])\s*--$")
_CACHE_TTL_SECONDS = 3600
_catalog_cache: dict[str, Any] = {"expires_at": 0.0, "acts": []}


def _base_url() -> str:
    return settings.BCLAWS_API_URL.rstrip("/")


def _content_url(*parts: str) -> str:
    path = "/".join(["civix", "content", _ASPECT, _INDEX, *parts])
    return urljoin(_base_url() + "/", path)


def document_url(document_id: str) -> str:
    """Canonical HTML document URL for a statute (matches publisher ``legalAct`` links)."""
    path = f"civix/document/id/{_ASPECT}/{_INDEX}/{document_id}"
    return urljoin(_base_url() + "/", path)


def _text(element: ET.Element, tag: str) -> str | None:
    child = element.find(tag)
    if child is None or child.text is None:
        return None
    return child.text.strip()


def _parse_content_listing(xml_text: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as err:
        raise HTTPException(
            status_code=502, detail=f"BC Laws returned invalid XML: {err}"
        ) from err

    entries: list[dict[str, Any]] = []
    for tag in ("dir", "document"):
        for element in root.findall(tag):
            title = _text(element, "CIVIX_DOCUMENT_TITLE")
            doc_id = _text(element, "CIVIX_DOCUMENT_ID")
            if not title or not doc_id:
                continue
            entry: dict[str, Any] = {
                "title": title,
                "folderId": doc_id,
                "type": _text(element, "CIVIX_DOCUMENT_TYPE"),
                "indexId": _text(element, "CIVIX_INDEX_ID"),
            }
            status = _text(element, "CIVIX_DOCUMENT_STATUS")
            if status:
                entry["status"] = status
            entries.append(entry)
    return entries


def _fetch_content(*path_parts: str) -> list[dict[str, Any]]:
    url = _content_url(*path_parts)
    try:
        response = requests.get(url, timeout=120, headers={"Accept": "application/xml"})
    except requests.RequestException as err:
        raise HTTPException(
            status_code=502, detail=f"Failed to reach BC Laws: {err}"
        ) from err
    if not response.ok:
        raise HTTPException(
            status_code=502,
            detail=f"BC Laws content request failed ({response.status_code}): {url}",
        )
    return _parse_content_listing(response.text)


def _guess_document_id(folder_id: str) -> str:
    if folder_id.endswith("_01"):
        return folder_id
    if folder_id.endswith("rep"):
        return f"{folder_id}_01"
    return f"{folder_id}_01"


def _resolve_act_document_id(letter_id: str, folder_id: str) -> str:
    """Fetch act folder and return the primary statute ``CIVIX_DOCUMENT_ID``."""
    for entry in _fetch_content(letter_id, folder_id):
        if entry.get("type") == "document" and entry.get("folderId"):
            return entry["folderId"]
    return _guess_document_id(folder_id)


def _act_name_from_title(title: str) -> str:
    """``Petroleum and Natural Gas Act [RSBC 1996] c. 361`` → short name."""
    bracket = title.find(" [")
    if bracket > 0:
        return title[:bracket].strip()
    return title.strip()


def _load_catalog(*, resolve_documents: bool = False) -> list[dict[str, Any]]:
    now = time.monotonic()
    cache_key = "resolved" if resolve_documents else "guessed"
    cached = _catalog_cache.get(cache_key)
    if cached and _catalog_cache.get(f"expires_at_{cache_key}", 0) > now:
        return cached

    letters = _fetch_content()
    acts: list[dict[str, Any]] = []

    for letter_entry in letters:
        match = _LETTER_RE.match(letter_entry["title"])
        if not match:
            continue
        letter = match.group(1)
        letter_id = letter_entry["folderId"]
        for act_entry in _fetch_content(letter_id):
            title = act_entry["title"]
            if "Act" not in title:
                continue
            folder_id = act_entry["folderId"]
            if resolve_documents:
                document_id = _resolve_act_document_id(letter_id, folder_id)
            else:
                document_id = _guess_document_id(folder_id)
            acts.append(
                {
                    "name": _act_name_from_title(title),
                    "title": title,
                    "letter": letter,
                    "folderId": folder_id,
                    "documentId": document_id,
                    "id": document_url(document_id),
                    "status": act_entry.get("status"),
                }
            )

    _catalog_cache[cache_key] = acts
    _catalog_cache[f"expires_at_{cache_key}"] = now + _CACHE_TTL_SECONDS
    return acts


def list_public_acts(
    *,
    letter: str | None = None,
    q: str | None = None,
    include_repealed: bool = False,
    resolve_documents: bool = False,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    acts = _load_catalog(resolve_documents=resolve_documents)

    if letter:
        letter = letter.upper()[:1]
        acts = [a for a in acts if a["letter"] == letter]

    if not include_repealed:
        acts = [a for a in acts if a.get("status") != "Repealed"]

    if q:
        term = q.casefold()
        acts = [
            a
            for a in acts
            if term in a["name"].casefold() or term in a["title"].casefold()
        ]

    total = len(acts)
    page = acts[offset : offset + limit]
    return {
        "source": "bclaws",
        "aspect": _ASPECT,
        "index": _INDEX,
        "license": f"{_base_url()}/standards/2014/QP-License_1.0.html",
        "total": total,
        "offset": offset,
        "limit": limit,
        "acts": page,
    }


def get_act_metadata(document_id: str) -> dict[str, Any]:
    """Resolve title and currency date from the statute HTML page."""
    url = document_url(document_id)
    try:
        info = Soup(url).legal_act_info()
    except Exception as err:
        raise HTTPException(
            status_code=502,
            detail=f"Could not parse BC Laws document {document_id}: {err}",
        ) from err
    return {
        "id": info["id"],
        "name": info["title"],
        "effectiveDate": info["effectiveDate"],
        "documentId": document_id,
    }


def list_directory_roles(*, q: str, limit: int = 20) -> dict[str, Any]:
    """Search BC Government Directory people by title for issuer name suggestions."""
    term = (q or "").strip()
    if not term:
        return {"source": "gtds", "total": 0, "limit": limit, "roles": []}

    url = settings.GTDS_URL
    params = {
        "view": "brief",
        "sortBy": "title",
        "for": "people",
        "attribute": "title",
        "matchMethod": "contains",
        "searchString": term,
    }
    try:
        response = requests.get(url, params=params, timeout=120)
    except requests.RequestException as err:
        raise HTTPException(
            status_code=502, detail=f"Failed to reach BC Directory: {err}"
        ) from err
    if not response.ok:
        raise HTTPException(
            status_code=502,
            detail=f"BC Directory request failed ({response.status_code}): {url}",
        )

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(response.text, "html.parser")
    roles: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        if "objectId=" not in href:
            continue
        person_name = " ".join(link.get_text(" ", strip=True).split())
        tr = link.find_parent("tr")
        if not tr:
            continue
        non_empty_cells = [
            " ".join(td.get_text(" ", strip=True).split())
            for td in tr.find_all("td")
            if " ".join(td.get_text(" ", strip=True).split())
        ]
        # Expected compact row order: [name, orgCode, orgUnit, title, phone]
        org_code = non_empty_cells[1] if len(non_empty_cells) > 1 else ""
        org_unit = non_empty_cells[2] if len(non_empty_cells) > 2 else ""
        title = non_empty_cells[3] if len(non_empty_cells) > 3 else ""
        phone = non_empty_cells[4] if len(non_empty_cells) > 4 else ""
        if not title:
            continue
        dedupe = (person_name, title)
        if dedupe in seen:
            continue
        seen.add(dedupe)
        roles.append(
            {
                "name": person_name,
                "title": title,
                "orgCode": org_code,
                "organizationalUnit": org_unit,
                "telephone": phone,
            }
        )
        if len(roles) >= limit:
            break

    return {
        "source": "gtds",
        "query": term,
        "total": len(roles),
        "limit": limit,
        "roles": roles,
    }
