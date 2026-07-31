"""
Bundled artefacts: canonical URL -> local relative file path.

The files live under ``untp/bundled/``. Schema URLs are UNTP artefacts; context URLs
include UNTP vocabulary and W3C VCDM 2.0 (for offline JSON-LD processing).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Directory under ``untp/bundled/`` (schemas, contexts, digests for this UNTP release).
BUNDLE_VERSION: str = "0.7.0"

CONTEXT_BUNDLE: dict[str, dict[str, str]] = {
    "https://www.w3.org/ns/credentials/v2": {
        "path": "v0.7.0/contexts/credentials-v2.jsonld",
        "digest": "sha256:59955ced6697d61e03f2b2556febe5308ab16842846f5b586d7f1f7adec92734",
    },
    "https://vocabulary.uncefact.org/untp/0.7.0/context/": {
        "path": "v0.7.0/contexts/untp.jsonld",
        "digest": "sha256:e99627e9ddd159eb0d80c8bba9634ca9099597cc547a37246ad3a4ee6384687e",
    },
    # Minimal offline stub so TemplateRenderMethod @context resolves during validation.
    "https://w3id.org/vc/render-method/v2rc2": {
        "path": "v0.7.0/contexts/render-method-v2rc2.jsonld",
        "digest": "sha256:501e49b04a5d4efe5b1096bbbacebefab8d06693260f138bc537a808e494889a",
    },
}


def bundled_context_digests_for_document(data: Mapping[str, Any]) -> dict[str, str]:
    """
    For each string URL in ``@context`` that appears in :data:`CONTEXT_BUNDLE`,
    return that URL mapped to its registered ``digest`` (``sha256:`` hex).
    """
    ctx = data.get("@context")
    if ctx is None:
        return {}
    urls: list[str] = []
    if isinstance(ctx, str):
        urls = [ctx]
    elif isinstance(ctx, list):
        for item in ctx:
            if isinstance(item, str):
                urls.append(item)
    out: dict[str, str] = {}
    for url in urls:
        entry = CONTEXT_BUNDLE.get(url)
        if entry is not None:
            out[url] = entry["digest"]
    return out


SCHEMA_BUNDLE: dict[str, dict[str, str]] = {
    "https://untp.unece.org/artefacts/schema/v0.7.0/dcc/ConformityAttestation.json": {
        "path": "v0.7.0/schemas/ConformityAttestation.json",
        "digest": "sha256:37e208da3b34a4c07c49776684ef079c5c8481541a9997afc3db9f4f874ccc2f",
    },
    "https://untp.unece.org/artefacts/schema/v0.7.0/dcc/ConformityCredential.json": {
        "path": "v0.7.0/schemas/ConformityCredential.json",
        "digest": "sha256:68e488ea1c2df8ff868ba63414a215087c2fb737b1b8f84909497ff32ee69f63",
    },
}

# Current default UNTP DCC context URL used by the plugin.
DEFAULT_DCC_CONTEXT_URL = "https://vocabulary.uncefact.org/untp/0.7.0/context/"
