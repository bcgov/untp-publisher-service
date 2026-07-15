"""Load issuer publication configs from ``configs/issuers.yaml`` and per-credential assets."""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException

from config import basedir, config_root, repo_root, settings


def issuers_file() -> Path:
    return config_root() / "issuers.yaml"


def credentials_dir() -> Path:
    """``configs/credentials/{type}/{version}/`` asset trees."""
    return config_root() / "credentials"


def issuer_did_from_alias(alias: str) -> str:
    """
    Build ``did:web:{domain}:{namespace}:{alias}`` from an ``issuers.yaml`` id.

    ``alias`` is the yaml ``id`` (e.g. ``mines-act:chief-permitting-officer``).
    Domain is the hostname of ``WEBVH_SERVER_URL``. Full DIDs are returned unchanged.
    """
    value = (alias or "").strip()
    if not value:
        raise HTTPException(status_code=500, detail="Issuer id/alias must not be empty")
    if value.startswith("did:"):
        return value
    try:
        domain = settings.publisher_domain()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not domain:
        raise HTTPException(
            status_code=500,
            detail=(
                "Cannot build issuer DID: set WEBVH_SERVER_URL "
                "(hostname is taken from that URL)"
            ),
        )
    return f"did:web:{domain}:{value.lstrip(':')}"


def _expand_issuer(instance: dict[str, Any]) -> dict[str, Any]:
    """Normalize issuer fields: yaml ``id`` becomes ``alias``; ``id`` is the full DID."""
    raw_id = (instance.get("id") or "").strip()
    if not raw_id:
        return {}
    issuer = {key: value for key, value in instance.items() if key != "credentials"}
    if raw_id.startswith("did:"):
        issuer["id"] = raw_id
        issuer.setdefault("alias", raw_id)
    else:
        issuer["alias"] = raw_id
        issuer["id"] = issuer_did_from_alias(raw_id)
    return issuer


def _load_yaml_file(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=500,
            detail=f"Invalid publication config {path.name}: expected mapping",
        )
    return data


def _normalize_instance(instance: dict[str, Any]) -> dict[str, Any]:
    """Map an ``instances[]`` entry to ``{issuer, credentials}``."""
    credentials = instance.get("credentials") or []
    if not isinstance(credentials, list):
        raise HTTPException(
            status_code=500,
            detail="issuers.yaml instance credentials must be a list",
        )
    issuer = _expand_issuer(instance)
    if not issuer:
        raise HTTPException(
            status_code=500,
            detail="issuers.yaml instance is missing id",
        )
    return {"issuer": issuer, "credentials": credentials}


@lru_cache(maxsize=1)
def _publication_index() -> dict[str, Any]:
    by_type: dict[str, dict[str, Any]] = {}
    by_issuer_id: dict[str, dict[str, Any]] = {}

    path = issuers_file()
    if not path.is_file():
        return {"by_type": by_type, "by_issuer_id": by_issuer_id, "path": path}

    doc = _load_yaml_file(path)
    instances = doc.get("instances")
    if instances is None:
        raise HTTPException(
            status_code=500,
            detail="issuers.yaml must define an instances list",
        )
    if not isinstance(instances, list):
        raise HTTPException(
            status_code=500,
            detail="issuers.yaml instances must be a list",
        )

    for instance in instances:
        if not isinstance(instance, dict):
            continue
        config = _normalize_instance(instance)
        issuer = config["issuer"]
        issuer_id = (issuer.get("id") or "").strip()
        if not issuer_id:
            continue
        by_issuer_id[issuer_id] = {"path": path, "config": config}

        for credential in config["credentials"]:
            if not isinstance(credential, dict):
                continue
            cred_type = (credential.get("type") or "").strip()
            if not cred_type:
                continue
            by_type[cred_type] = {
                "path": path,
                "config": config,
                "credential": credential,
            }

    return {"by_type": by_type, "by_issuer_id": by_issuer_id, "path": path}


def list_issuer_instances() -> list[dict[str, Any]]:
    """Issuer instances from ``configs/issuers.yaml`` (``instances[]``).

    Each item is the expanded issuer dict (full DID in ``id``, original yaml id in
    ``alias``) plus retained ``credentials[]`` entries from the yaml instance.
    """
    issuers: list[dict[str, Any]] = []
    for entry in _publication_index()["by_issuer_id"].values():
        config = entry["config"]
        issuer = dict(config["issuer"])
        issuer["credentials"] = list(config["credentials"])
        issuers.append(issuer)
    return issuers


def _credential_entry(credential_type: str) -> dict[str, Any]:
    entry = _publication_index()["by_type"].get(credential_type)
    if not entry:
        raise HTTPException(
            status_code=404,
            detail=f"No publication config for credential type {credential_type!r}",
        )
    return entry


def credential_yaml_entry(credential_type: str) -> dict[str, Any]:
    """Issuers.yaml credential entry (type, version, …)."""
    return dict(_credential_entry(credential_type)["credential"])


def load_publication_config(credential_type: str) -> dict[str, Any]:
    """Issuer config plus the matching ``credentials[]`` entry for ``credential_type``."""
    entry = _credential_entry(credential_type)
    return {
        "issuer": entry["config"].get("issuer") or {},
        "credential": entry["credential"],
    }


def load_publication_config_by_issuer(issuer_id: str) -> dict[str, Any]:
    entry = _publication_index()["by_issuer_id"].get(issuer_id)
    if not entry:
        raise HTTPException(
            status_code=404,
            detail=f"No publication config for issuer {issuer_id!r}",
        )
    return entry["config"]


def credential_version_for_type(credential_type: str) -> str:
    credential = _credential_entry(credential_type)["credential"]
    return (credential.get("version") or "v1.0").strip()


def _path_under(root: Path, *parts: str) -> Path:
    """Join ``parts`` under ``root``; reject absolute segments and ``..`` escape."""
    if not parts:
        raise HTTPException(status_code=400, detail="Invalid config path")
    root_resolved = root.resolve()
    segments: list[str] = []
    for part in parts:
        text = str(part).strip()
        if not text:
            raise HTTPException(status_code=400, detail="Invalid config path")
        piece = Path(text)
        if piece.is_absolute():
            raise HTTPException(status_code=400, detail="Invalid config path")
        for segment in piece.parts:
            if segment in ("", ".", ".."):
                raise HTTPException(status_code=400, detail="Invalid config path")
            segments.append(segment)
    candidate = root_resolved.joinpath(*segments).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid config path") from exc
    return candidate


def credential_set_dir(credential_type: str) -> Path:
    """``configs/credentials/{type}/{version}/`` — inferred from publication config."""
    version = credential_version_for_type(credential_type)
    return _path_under(credentials_dir(), credential_type, version)


def _asset_path(credential_type: str, filename: str) -> Path:
    version = credential_version_for_type(credential_type)
    return _path_under(credentials_dir(), credential_type, version, filename)


def _template_path_for_type(credential_type: str, credential: dict[str, Any]) -> Path:
    template_path = credential.get("template")
    if isinstance(template_path, str) and template_path.strip():
        return resolve_config_path(template_path.strip())
    version = credential_version_for_type(credential_type)
    return _path_under(credentials_dir(), credential_type, version, "template.yaml")


def sample_publication_payload_path(credential_type: str) -> Path:
    return _asset_path(credential_type, "payload.json")


def data_schema_path(credential_type: str) -> Path:
    """``configs/credentials/{type}/{version}/data.schema.json``."""
    return _asset_path(credential_type, "data.schema.json")


def load_data_schema(credential_type: str) -> dict[str, Any]:
    """JSON Schema for the publish ``data`` object (required per credential type)."""
    return _load_json_file(
        data_schema_path(credential_type),
        label="publication data schema",
    )


def sample_issued_credential_path(credential_type: str) -> Path:
    return _asset_path(credential_type, "sample.json")


def oca_bundle_path(credential_type: str) -> Path:
    """``configs/credentials/{type}/{version}/oca.json`` — inferred from publication config."""
    explicit = (
        _credential_entry(credential_type)["credential"].get("assets", {}).get("ocaBundle")
    )
    if isinstance(explicit, str) and explicit.strip():
        return resolve_repo_path(explicit.strip())
    return _asset_path(credential_type, "oca.json")


def _load_json_file(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        try:
            relative = path.relative_to(config_root())
        except ValueError:
            relative = path
        raise HTTPException(
            status_code=404,
            detail=f"No {label} at {relative}",
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=500,
            detail=f"Invalid {label} {path.name}: expected JSON object",
        )
    return data


def load_sample_publication_payload(credential_type: str) -> dict[str, Any]:
    return _load_json_file(
        sample_publication_payload_path(credential_type),
        label="publication payload sample",
    )


def load_sample_issued_credential_optional(
    credential_type: str | None,
) -> dict[str, Any] | None:
    if not credential_type:
        return None
    path = sample_issued_credential_path(credential_type)
    if not path.is_file():
        return None
    return _load_json_file(path, label="issued credential sample")


def load_oca_bundle(credential_type: str) -> dict[str, Any]:
    path = oca_bundle_path(credential_type)
    if not path.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"No OCA bundle for credential type {credential_type!r} at {path}",
        )
    return _load_json_file(path, label="OCA bundle")


def load_credential_template_source(credential_type: str) -> str:
    """Raw credential template YAML (may contain Jinja)."""
    credential = _credential_entry(credential_type)["credential"]
    if isinstance(credential.get("template"), dict):
        raise HTTPException(
            status_code=500,
            detail=f"Inline credential template for {credential_type!r} has no source text",
        )
    path = _template_path_for_type(credential_type, credential)
    if not path.is_file():
        try:
            relative = path.relative_to(config_root())
        except ValueError:
            relative = path
        raise HTTPException(
            status_code=500,
            detail=(
                f"No credential template for type {credential_type!r} "
                f"(expected {relative})"
            ),
        )
    return path.read_text(encoding="utf-8")


def load_credential_template_source_optional(credential_type: str | None) -> str | None:
    if not credential_type or credential_type not in _publication_index()["by_type"]:
        return None
    return load_credential_template_source(credential_type)


def load_credential_template(credential_type: str) -> dict[str, Any]:
    """Parse credential template after rendering with a stub context."""
    from app.services.templates import (
        materialize_credential_document,
        template_stub_context,
    )

    credential = _credential_entry(credential_type)["credential"]
    if isinstance(credential.get("template"), dict):
        return credential["template"]
    return materialize_credential_document(
        load_credential_template_source(credential_type),
        template_stub_context(),
    )


def load_publisher_extension_context() -> dict[str, Any]:
    """JSON-LD document for publisher terms (``SimpleRefreshQuery``, ``OCABundle``)."""
    path = config_root() / "contexts" / "publisher-v1.jsonld"
    if not path.is_file():
        raise FileNotFoundError(f"Publisher extension context missing at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_config_path(relative_path: str) -> Path:
    """Resolve a path relative to ``config_root()`` (e.g. ``credentials/…``)."""
    path = _path_under(config_root(), relative_path.strip())
    if not path.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"Config file missing: {relative_path}",
        )
    return path


def resolve_repo_path(relative_path: str) -> Path:
    rel = Path(relative_path.strip())
    if rel.is_absolute() or not rel.parts:
        raise HTTPException(status_code=400, detail="Invalid config path")
    if rel.parts[0] == "backend":
        path = _path_under(Path(basedir), *rel.parts[1:])
    elif rel.parts[0] == "app":
        path = _path_under(Path(basedir), *rel.parts)
    else:
        path = _path_under(repo_root(), *rel.parts)
    if not path.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"Config asset missing: {relative_path}",
        )
    return path
