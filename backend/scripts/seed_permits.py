#!/usr/bin/env python3
"""Seed local/dev publisher with Mines Act permit publications.

Reads a JSON list of ``POST /credentials/publish`` bodies and publishes each
using the admin ``X-API-Key`` (same value as ``TRACTION_API_KEY``).

Payloads later in the file may:

- reuse the same entity + cardinality (permittee + permit ids) with changed
  ``data`` so the publisher marks the prior record ``refresh: true`` and issues
  a new iteration — useful for Discovery collapse; or
- reuse the same cardinality (permit id) with a *different* entity (permittee
  id) so Discovery shows two entries under one permit number.

Example::

    cd backend
    uv run python scripts/seed_permits.py
    uv run python scripts/seed_permits.py --base-url http://127.0.0.1:8000 \\
        --payloads scripts/seed_data/mines_act_permits.json

Environment (optional overrides)::

    PUBLISHER_BASE_URL   default http://127.0.0.1:8000
    TRACTION_API_KEY     admin X-API-Key (falls back to backend/.env via Settings)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

# Allow ``from config import settings`` when run as a script from backend/.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from config import settings  # noqa: E402

DEFAULT_PAYLOADS = Path(__file__).resolve().parent / "seed_data" / "mines_act_permits.json"


def _load_payloads(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit(f"Expected a JSON array in {path}")
    payloads: list[dict] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SystemExit(f"Item {i} in {path} is not an object")
        comment = str(item.get("comment") or "").strip()
        body = {k: v for k, v in item.items() if k != "comment"}
        if "template" not in body or "version" not in body or "data" not in body:
            raise SystemExit(
                f"Item {i} missing template/version/data (comment-only keys stripped)"
            )
        body["_comment"] = comment
        payloads.append(body)
    return payloads


def _identity(body: dict) -> tuple[str, str]:
    data = body.get("data") or {}
    permit = data.get("permit") or {}
    permittee = data.get("permittee") or {}
    return (
        str(permittee.get("identifier") or "").strip(),
        str(permit.get("identifier") or "").strip(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=None,
        help="Publisher origin (default: PUBLISHER_BASE_URL or http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--payloads",
        type=Path,
        default=DEFAULT_PAYLOADS,
        help=f"JSON array of publish bodies (default: {DEFAULT_PAYLOADS})",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Admin X-API-Key (default: TRACTION_API_KEY from env / .env)",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Exit on first non-2xx response",
    )
    args = parser.parse_args()

    base = (
        args.base_url
        or __import__("os").environ.get("PUBLISHER_BASE_URL")
        or "http://127.0.0.1:8000"
    ).rstrip("/")
    api_key = (args.api_key or settings.TRACTION_API_KEY or "").strip()
    if not api_key:
        print("No API key: set TRACTION_API_KEY or pass --api-key", file=sys.stderr)
        return 2

    payloads = _load_payloads(args.payloads)
    print(f"Publishing {len(payloads)} payload(s) to {base}/credentials/publish")

    ok = 0
    failed = 0
    skipped = 0
    republications = 0
    seen_identities: set[tuple[str, str]] = set()

    with httpx.Client(timeout=120.0) as client:
        for i, body in enumerate(payloads, start=1):
            comment = body.pop("_comment", "")
            entity, cardinality = _identity(body)
            label = f"{entity or '?'} / {cardinality or '?'}"
            is_republication = (entity, cardinality) in seen_identities and bool(
                entity and cardinality
            )
            kind = "reissue" if is_republication else "issue"
            note = f" — {comment}" if comment else ""

            try:
                response = client.post(
                    f"{base}/credentials/publish",
                    headers={
                        "X-API-Key": api_key,
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json=body,
                )
            except httpx.HTTPError as exc:
                failed += 1
                print(f"[{i}/{len(payloads)}] FAIL {kind} {label}: {exc}")
                if args.stop_on_error:
                    return 1
                continue

            if response.status_code == 201:
                ok += 1
                if is_republication:
                    republications += 1
                if entity and cardinality:
                    seen_identities.add((entity, cardinality))
                cred_id = ""
                try:
                    cred_id = response.json().get("credentialId") or ""
                except Exception:
                    pass
                print(
                    f"[{i}/{len(payloads)}] OK   {kind} {label} → "
                    f"{cred_id or response.status_code}{note}"
                )
            elif response.status_code == 200:
                skipped += 1
                if entity and cardinality:
                    seen_identities.add((entity, cardinality))
                cred_id = ""
                try:
                    cred_id = response.json().get("credentialId") or ""
                except Exception:
                    pass
                print(
                    f"[{i}/{len(payloads)}] SKIP unchanged {label} → "
                    f"{cred_id or 200}{note}"
                )
            else:
                failed += 1
                detail = response.text[:500]
                print(
                    f"[{i}/{len(payloads)}] FAIL {kind} {label}: "
                    f"HTTP {response.status_code} {detail}"
                )
                if args.stop_on_error:
                    return 1

    print(
        f"Done: {ok} issued ({republications} republications), "
        f"{skipped} unchanged, {failed} failed"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
