"""Load issuer publication configs from ``configs/publications/`` (one issuer per file)."""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException

from config import basedir, config_root, repo_root


def publications_dir() -> Path:
    return config_root() / "publications"


def templates_dir() -> Path:
    return config_root() / "templates"


def samples_dir() -> Path:
    return config_root() / "samples"


def _load_yaml_file(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=500,
            detail=f"Invalid publication config {path.name}: expected mapping",
        )
    return data


@lru_cache(maxsize=1)
def _publication_index() -> dict[str, Any]:
    by_type: dict[str, dict[str, Any]] = {}
    by_issuer_id: dict[str, dict[str, Any]] = {}

    if not publications_dir().is_dir():
        return {"by_type": by_type, "by_issuer_id": by_issuer_id}

    for path in sorted(publications_dir().glob("*.yaml")):
        doc = _load_yaml_file(path)
        issuer = doc.get("issuer") or {}
        issuer_id = (issuer.get("id") or "").strip()
        if not issuer_id:
            continue
        by_issuer_id[issuer_id] = {"path": path, "config": doc}

        for credential in doc.get("credentials") or []:
            if not isinstance(credential, dict):
                continue
            cred_type = (credential.get("type") or "").strip()
            if not cred_type:
                continue
            by_type[cred_type] = {
                "path": path,
                "config": doc,
                "credential": credential,
            }

    return {"by_type": by_type, "by_issuer_id": by_issuer_id}


def _credential_entry(credential_type: str) -> dict[str, Any]:
    entry = _publication_index()["by_type"].get(credential_type)
    if not entry:
        raise HTTPException(
            status_code=404,
            detail=f"No publication config for credential type {credential_type!r}",
        )
    return entry


def load_publication_config(credential_type: str) -> dict[str, Any]:
    """Issuer config plus the matching ``credentials[]`` entry for ``credential_type``."""
    entry = _credential_entry(credential_type)
    return {
        "issuer": entry["config"].get("issuer") or {},
        "credential": entry["credential"],
    }


def load_publication_config_optional(credential_type: str | None) -> dict[str, Any] | None:
    if not credential_type:
        return None
    entry = _publication_index()["by_type"].get(credential_type)
    if not entry:
        return None
    return load_publication_config(credential_type)


def load_publication_config_by_issuer(issuer_id: str) -> dict[str, Any]:
    entry = _publication_index()["by_issuer_id"].get(issuer_id)
    if not entry:
        raise HTTPException(
            status_code=404,
            detail=f"No publication config for issuer {issuer_id!r}",
        )
    return entry["config"]


def load_publication_config_by_issuer_optional(issuer_id: str | None) -> dict[str, Any] | None:
    if not issuer_id:
        return None
    entry = _publication_index()["by_issuer_id"].get(issuer_id)
    if not entry:
        return None
    return entry["config"]


def _credential_version(credential: dict[str, Any]) -> str:
    return (credential.get("version") or "v1.0").strip()


def _template_path_for_type(credential_type: str, credential: dict[str, Any]) -> Path:
    template_ref = credential.get("template")
    if isinstance(template_ref, str) and template_ref.strip():
        return resolve_config_path(template_ref.strip())
    version = _credential_version(credential)
    versioned = templates_dir() / f"{credential_type}.{version}.yaml"
    if versioned.is_file():
        return versioned
    return templates_dir() / f"{credential_type}.yaml"


def credential_version_for_type(credential_type: str) -> str:
    return _credential_version(_credential_entry(credential_type)["credential"])


def sample_set_dir(credential_type: str) -> Path:
    """``configs/samples/{type}.{version}/`` — inferred from publication config."""
    version = credential_version_for_type(credential_type)
    return samples_dir() / f"{credential_type}.{version}"


def sample_publication_payload_path(credential_type: str) -> Path:
    return sample_set_dir(credential_type) / "publication-payload.json"


def sample_issued_credential_path(credential_type: str) -> Path:
    return sample_set_dir(credential_type) / "issued-credential.json"


def _load_json_file(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"No {label} at {path.relative_to(config_root())}",
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=500,
            detail=f"Invalid {label} {path.name}: expected JSON object",
        )
    return data


def load_sample_publication_payload(credential_type: str) -> dict[str, Any]:
    path = sample_publication_payload_path(credential_type)
    return _load_json_file(path, label="publication payload sample")


def load_sample_publication_payload_optional(
    credential_type: str | None,
) -> dict[str, Any] | None:
    if not credential_type:
        return None
    path = sample_publication_payload_path(credential_type)
    if not path.is_file():
        return None
    return load_sample_publication_payload(credential_type)


def load_sample_issued_credential_optional(credential_type: str | None) -> dict[str, Any] | None:
    if not credential_type:
        return None
    path = sample_issued_credential_path(credential_type)
    if not path.is_file():
        return None
    return _load_json_file(path, label="issued credential sample")


def load_credential_template(credential_type: str) -> dict[str, Any]:
    credential = _credential_entry(credential_type)["credential"]
    if isinstance(credential.get("template"), dict):
        return credential["template"]
    path = _template_path_for_type(credential_type, credential)
    if not path.is_file():
        raise HTTPException(
            status_code=500,
            detail=(
                f"No credential template for type {credential_type!r} "
                f"(expected {path.relative_to(config_root())})"
            ),
        )
    return _load_yaml_file(path)


def load_credential_template_optional(credential_type: str | None) -> dict[str, Any] | None:
    if not credential_type:
        return None
    if credential_type not in _publication_index()["by_type"]:
        return None
    return load_credential_template(credential_type)


def list_publication_config_types() -> list[str]:
    return sorted(_publication_index()["by_type"].keys())


def resolve_config_path(relative_path: str) -> Path:
    """Resolve a path relative to ``config_root()`` (e.g. ``templates/…``)."""
    path = config_root() / relative_path
    if not path.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"Config file missing: {relative_path}",
        )
    return path


def resolve_repo_path(relative_path: str) -> Path:
    rel = Path(relative_path)
    if rel.parts and rel.parts[0] == "backend":
        path = Path(basedir) / Path(*rel.parts[1:])
    elif rel.parts and rel.parts[0] == "app":
        path = Path(basedir) / rel
    else:
        path = repo_root() / rel
    if not path.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"Config asset missing: {relative_path}",
        )
    return path
