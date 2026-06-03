#!/usr/bin/env python3
"""
Build an OCA bundle (JSON Pointer attributes) from a manifest.

Manifest: captureBaseId, attributes {pointer: type}, flagged_attributes, optional overlays.

Usage (from backend/):
  uv run python scripts/build_oca_bundle.py \\
    --manifest app/examples/untp_v0_7_0_dcc_battery_oca_manifest.json \\
    --output app/examples/untp_v0_7_0_dcc_battery_oca_bundle.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def pointer_label(pointer: str) -> str:
    """Human label from JSON Pointer leaf and parent context."""
    parts = [p for p in pointer.split("/") if p and not p.isdigit()]
    leaf = parts[-1] if parts else pointer
    # camelCase / id → words
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


def default_overlays(capture_base_id: str, attributes: dict[str, str]) -> list[dict]:
    pointers = list(attributes.keys())
    """en label + information overlays; labels derived from pointers when not curated."""
    labels = {p: pointer_label(p) for p in pointers}
    information = {
        p: f"Value at {p} in the digital conformity credential."
        for p in pointers
    }

    def pick(source: dict) -> dict:
        return {k: source[k] for k in pointers if k in source}

    sensitive = [
        p
        for p in pointers
        if any(
            seg in p
            for seg in (
                "/registeredId",
                "/imageData",
                "/auditableEvidence/",
            )
        )
    ]
    datetime_pointers = [p for p, t in attributes.items() if t == "DateTime"]

    overlays: list[dict] = [
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
    return overlays


def build_bundle(manifest: dict, include_default_overlays: bool) -> dict:
    capture_base_id = manifest["captureBaseId"]
    attributes = manifest["attributes"]
    pointers = list(attributes.keys())

    bundle: dict = {
        "type": "spec/capture_base/1.0",
        "attributes": attributes,
        "flagged_attributes": manifest.get("flagged_attributes", []),
    }

    overlays = manifest.get("overlays")
    if overlays is None and include_default_overlays:
        overlays = default_overlays(capture_base_id, attributes)
    if overlays:
        bundle["overlays"] = overlays
        for overlay in bundle["overlays"]:
            overlay.setdefault("capture_base", capture_base_id)

    return bundle


def main() -> None:
    parser = argparse.ArgumentParser(description="Build OCA bundle from JSON Pointer manifest")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--with-default-overlays",
        action="store_true",
        help="Add default label/information/sensitive overlays (off by default)",
    )
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    bundle = build_bundle(manifest, include_default_overlays=args.with_default_overlays)
    if not args.with_default_overlays and "overlays" in bundle:
        del bundle["overlays"]

    args.output.write_text(
        json.dumps(bundle, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.output} ({len(bundle['attributes'])} attributes)")


if __name__ == "__main__":
    main()
