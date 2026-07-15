#!/usr/bin/env python3
"""OCA authoring: credential instance → manifest → ``oca.json``.

Usage (from backend/)::

  # Instance → manifest
  uv run python scripts/oca_builder.py manifest \\
    --instance ../configs/credentials/BCMinesActPermitCredential/v1.1/sample.json \\
    --output /tmp/manifest.json \\
    --capture-base-id bc-mines-act-permit-v1.1

  # Manifest → oca.json
  uv run python scripts/oca_builder.py bundle \\
    --manifest /tmp/manifest.json \\
    --output ../configs/credentials/BCMinesActPermitCredential/v1.1/oca.json

  # One shot: instance → oca.json
  uv run python scripts/oca_builder.py from-instance \\
    --instance ../configs/credentials/BCMinesActPermitCredential/v1.1/sample.json \\
    --output ../configs/credentials/BCMinesActPermitCredential/v1.1/oca.json \\
    --capture-base-id bc-mines-act-permit-v1.1
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


class OcaBuilder:
    """Author OCA manifests and capture-base bundles from credential instances."""

    SKIP_KEYS = frozenset({"@context", "type"})
    ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
    ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    SENSITIVE_SEGMENTS = frozenset({"registeredId", "imageData", "linkURL"})
    SENSITIVE_POINTER_SEGMENTS = (
        "/registeredId",
        "/imageData",
        "/auditableEvidence/",
    )

    def infer_oca_type(self, key: str, value: Any) -> str:
        if isinstance(value, bool):
            return "Boolean"
        if isinstance(value, (int, float)):
            return "Numeric"
        if isinstance(value, str):
            if key in ("validFrom", "validUntil") or self.ISO_DATETIME.match(value):
                return "DateTime"
            if key.endswith("Date") or self.ISO_DATE.match(value):
                return "DateTime"
        return "Text"

    def _walk(
        self,
        obj: Any,
        pointer: str,
        attributes: dict[str, str],
        flagged: list[str],
    ) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in self.SKIP_KEYS:
                    continue
                child = f"{pointer}/{key}" if pointer != "/" else f"/{key}"
                self._walk(value, child, attributes, flagged)
            return

        if isinstance(obj, list):
            for index, item in enumerate(obj):
                self._walk(item, f"{pointer}/{index}", attributes, flagged)
            return

        key = pointer.rsplit("/", 1)[-1]
        attributes[pointer] = self.infer_oca_type(key, obj)
        if any(seg in pointer for seg in self.SENSITIVE_SEGMENTS):
            if pointer not in flagged:
                flagged.append(pointer)

    def manifest_from_instance(
        self,
        instance: dict[str, Any],
        capture_base_id: str,
    ) -> dict[str, Any]:
        """Derive JSON Pointer attributes from a credential instance."""
        attributes: dict[str, str] = {}
        flagged: list[str] = []
        self._walk(instance, "/", attributes, flagged)
        return {
            "captureBaseId": capture_base_id,
            "attributes": dict(sorted(attributes.items())),
            "flagged_attributes": sorted(flagged),
        }

    @staticmethod
    def pointer_label(pointer: str) -> str:
        """Human label from JSON Pointer leaf and parent context."""
        parts = [p for p in pointer.split("/") if p and not p.isdigit()]
        leaf = parts[-1] if parts else pointer
        words = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", leaf)
        label = " ".join(w.capitalize() if w.islower() else w for w in words) or leaf
        if len(parts) > 1:
            parent = parts[-2]
            parent_words = re.findall(
                r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", parent
            )
            parent_label = " ".join(
                w.capitalize() if w.islower() else w for w in parent_words
            )
            if parent_label and parent_label.lower() not in label.lower():
                return f"{parent_label} — {label}"
        return label

    def default_overlays(
        self,
        capture_base_id: str,
        attributes: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Default en label / information / sensitive overlays from pointers."""
        pointers = list(attributes.keys())
        labels = {p: self.pointer_label(p) for p in pointers}
        information = {
            p: f"Value at {p} in the digital conformity credential."
            for p in pointers
        }

        def pick(source: dict[str, str]) -> dict[str, str]:
            return {k: source[k] for k in pointers if k in source}

        sensitive = [
            p
            for p in pointers
            if any(seg in p for seg in self.SENSITIVE_POINTER_SEGMENTS)
        ]
        datetime_pointers = [p for p, t in attributes.items() if t == "DateTime"]

        return [
            {
                "capture_base": capture_base_id,
                "type": "overlay/sensitive/2.0.0",
                "attributes": sensitive,
            },
            {
                "capture_base": capture_base_id,
                "type": "overlay/standard/2.0.0",
                "attribute_standards": {p: "ISO 8601" for p in datetime_pointers},
            },
            {
                "capture_base": capture_base_id,
                "type": "overlay/format/2.0.0",
                "attribute_formats": {p: "MMMM D, YYYY" for p in datetime_pointers},
            },
            {
                "capture_base": capture_base_id,
                "type": "spec/overlays/label/1.0",
                "language": "en",
                "attribute_labels": pick(labels),
            },
            {
                "capture_base": capture_base_id,
                "type": "spec/overlays/information/1.0",
                "language": "en",
                "attribute_information": pick(information),
            },
        ]

    def bundle_from_manifest(
        self,
        manifest: dict[str, Any],
        *,
        include_default_overlays: bool = False,
    ) -> dict[str, Any]:
        """Build an OCA capture-base bundle from a pointer manifest."""
        capture_base_id = manifest["captureBaseId"]
        attributes = manifest["attributes"]

        bundle: dict[str, Any] = {
            "type": "spec/capture_base/1.0",
            "attributes": attributes,
            "flagged_attributes": manifest.get("flagged_attributes", []),
        }

        overlays = manifest.get("overlays")
        if overlays is None and include_default_overlays:
            overlays = self.default_overlays(capture_base_id, attributes)
        if overlays:
            bundle["overlays"] = overlays
            for overlay in bundle["overlays"]:
                overlay.setdefault("capture_base", capture_base_id)

        return bundle

    def bundle_from_instance(
        self,
        instance: dict[str, Any],
        capture_base_id: str,
        *,
        include_default_overlays: bool = False,
    ) -> dict[str, Any]:
        """Instance → manifest → bundle in one step."""
        manifest = self.manifest_from_instance(instance, capture_base_id)
        return self.bundle_from_manifest(
            manifest,
            include_default_overlays=include_default_overlays,
        )

    @staticmethod
    def write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def load_json(path: Path) -> dict[str, Any]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path} must contain a JSON object")
        return data


def _cmd_manifest(args: argparse.Namespace) -> None:
    builder = OcaBuilder()
    instance = builder.load_json(args.instance)
    manifest = builder.manifest_from_instance(instance, args.capture_base_id)
    builder.write_json(args.output, manifest)
    print(
        f"Wrote {args.output}: {len(manifest['attributes'])} attributes, "
        f"{len(manifest['flagged_attributes'])} flagged"
    )


def _cmd_bundle(args: argparse.Namespace) -> None:
    builder = OcaBuilder()
    manifest = builder.load_json(args.manifest)
    bundle = builder.bundle_from_manifest(
        manifest,
        include_default_overlays=args.with_default_overlays,
    )
    builder.write_json(args.output, bundle)
    print(f"Wrote {args.output} ({len(bundle['attributes'])} attributes)")


def _cmd_from_instance(args: argparse.Namespace) -> None:
    builder = OcaBuilder()
    instance = builder.load_json(args.instance)
    bundle = builder.bundle_from_instance(
        instance,
        args.capture_base_id,
        include_default_overlays=args.with_default_overlays,
    )
    builder.write_json(args.output, bundle)
    print(f"Wrote {args.output} ({len(bundle['attributes'])} attributes)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Author OCA manifests and bundles from credential instances",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_manifest = sub.add_parser("manifest", help="Instance → OCA pointer manifest")
    p_manifest.add_argument("--instance", type=Path, required=True)
    p_manifest.add_argument("--output", type=Path, required=True)
    p_manifest.add_argument("--capture-base-id", required=True)
    p_manifest.set_defaults(func=_cmd_manifest)

    p_bundle = sub.add_parser("bundle", help="Manifest → oca.json")
    p_bundle.add_argument("--manifest", type=Path, required=True)
    p_bundle.add_argument("--output", type=Path, required=True)
    p_bundle.add_argument(
        "--with-default-overlays",
        action="store_true",
        help="Add default label/information/sensitive overlays",
    )
    p_bundle.set_defaults(func=_cmd_bundle)

    p_one = sub.add_parser("from-instance", help="Instance → oca.json")
    p_one.add_argument("--instance", type=Path, required=True)
    p_one.add_argument("--output", type=Path, required=True)
    p_one.add_argument("--capture-base-id", required=True)
    p_one.add_argument(
        "--with-default-overlays",
        action="store_true",
        help="Add default label/information/sensitive overlays",
    )
    p_one.set_defaults(func=_cmd_from_instance)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
