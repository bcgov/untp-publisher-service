#!/usr/bin/env python3
"""
Derive an OCA manifest (JSON Pointer attributes) from a credential instance JSON.

Skips @context and JSON-LD type arrays; walks scalars and array indices as in the example.

Usage (from backend/):
  uv run python scripts/instance_to_oca_manifest.py \\
    --instance app/examples/untp_v0_7_0_dcc_battery_instance.json \\
    --output app/examples/untp_v0_7_0_dcc_battery_oca_manifest.json \\
    --capture-base-id rba-vap-battery-conformity-demo
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

SKIP_KEYS = {"@context", "type"}
ISO_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
)
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SENSITIVE_SEGMENTS = {
    "registeredId",
    "imageData",
    "linkURL",
}


def infer_oca_type(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return "Boolean"
    if isinstance(value, int) or isinstance(value, float):
        return "Numeric"
    if isinstance(value, str):
        if key in ("validFrom", "validUntil") or ISO_DATETIME.match(value):
            return "DateTime"
        if key.endswith("Date") or ISO_DATE.match(value):
            return "DateTime"
    return "Text"


def walk(
    obj: Any,
    pointer: str,
    attributes: dict[str, str],
    flagged: list[str],
) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in SKIP_KEYS:
                continue
            child = f"{pointer}/{key}" if pointer != "/" else f"/{key}"
            walk(value, child, attributes, flagged)
        return

    if isinstance(obj, list):
        for index, item in enumerate(obj):
            walk(item, f"{pointer}/{index}", attributes, flagged)
        return

    # Scalar leaf
    key = pointer.rsplit("/", 1)[-1]
    attributes[pointer] = infer_oca_type(key, obj)
    if any(seg in pointer for seg in SENSITIVE_SEGMENTS):
        if pointer not in flagged:
            flagged.append(pointer)


def build_manifest(instance: dict, capture_base_id: str) -> dict:
    attributes: dict[str, str] = {}
    flagged: list[str] = []
    walk(instance, "/", attributes, flagged)
    return {
        "captureBaseId": capture_base_id,
        "attributes": dict(sorted(attributes.items())),
        "flagged_attributes": sorted(flagged),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build OCA manifest from credential instance")
    parser.add_argument("--instance", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--capture-base-id",
        default="rba-vap-battery-conformity-demo",
    )
    args = parser.parse_args()

    instance = json.loads(args.instance.read_text(encoding="utf-8"))
    manifest = build_manifest(instance, args.capture_base_id)
    args.output.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {args.output}: {len(manifest['attributes'])} attributes, "
        f"{len(manifest['flagged_attributes'])} flagged"
    )


if __name__ == "__main__":
    main()
