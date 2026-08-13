# UNTP Publisher — Backend

FastAPI backend for the UNTP Publisher service. See the [repository README](../README.md) for overview and operational docs.

## Setup

```bash
uv sync
```

## Run

```bash
uv run python main.py
# or
uv run uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

## Test suite mode (`TEST_SUITE`)

Set **`TEST_SUITE=true`** in the environment to run a **minimal** app: **`GET /server/status`**, **`POST /test-suite/validate`**, and **`POST /test-suite/build-credential`**. The publisher API (auth, credentials, templates, static) is **not** registered. Use this for isolated UNTP validation and credential templating in CI or local harnesses.

```bash
cd backend
TEST_SUITE=true PUBLISHER_DOMAIN=http://localhost:8000 uv run uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

Build an unsigned Mines Act credential from the sample publication payload:

```bash
curl -sS -X POST http://localhost:8000/test-suite/build-credential \
  -H 'Content-Type: application/json' \
  -d @../configs/credentials/BCMinesActPermitCredential/v1.1/payload.json \
  | jq .
```

Include **`template`**, **`version`**, and **`data`** on the publication payload.
Holder and permit ids are resolved from `data` via `x-publisher-pointers` in `data.schema.json`.

- **`POST /test-suite/validate`** — JSON body is the UNTP document; optional query **`kind`** (`dcc_credential` or `dcc_attestation`) skips automatic `type` detection.
- **`POST /test-suite/build-credential`** — JSON body is a publication payload (`template`, `version`, `data`); returns **`credential`** (unsigned, no `proof`) after UNTP validation (400 when invalid).
- Response (validate): **`success`**, **`validation_checks`** (same structure as the validator’s per-check report), **`artefact_kind`**, and **`error`** when validation fails.

When **`TEST_SUITE`** is unset or false, **`/test-suite/*`** routes are omitted entirely.

## UNTP bundled artefacts (DCC + DIA)

Vendored JSON lives under **`untp/bundled/`** (snapshots from [UNTP `artefacts` on GitLab](https://opensource.unicc.org/un/unece/uncefact/spec-untp/-/tree/main/artefacts)). **`untp/releases.py`** maps each **canonical published URL** (vocabulary context URL plus [untp.unece.org](https://untp.unece.org) schema URLs) to **`path`** and **`sha256` digest** via **`CONTEXT_BUNDLE`** and **`SCHEMA_BUNDLE`**. **`DEFAULT_DCC_CONTEXT_URL`** is the default `@context` for the DCC plugin. See **`untp/bundled/README.md`** for layout and how to add artefacts. A future MongoDB layer can store resolved documents keyed by those URLs.

## Docker

From the repo root (example local image name):

```bash
docker build -t untp-publisher-service -f backend/Dockerfile backend/
docker run -p 8000:8000 untp-publisher-service
```

The image published to GitHub Container Registry is **`ghcr.io/bcgov/untp-publisher-service`** (see `.github/workflows/image-publisher.yaml`). The build context is always `backend/`.

## Configuration notes

- **`PUBLISHER_DOMAIN`** — Public publisher host/origin used in credential IDs and related resource URLs (e.g. `http://localhost:8000` or `publisher.example.com`).
- **`OCA_DIGEST`** — When ``true``, OCA ``renderMethod`` includes ``digestMultibase`` of ``oca.json``. Default ``false``: omit the digest so the bundle at a stable URL can evolve without invalidating already-issued credentials. Only enable if OCA bytes for that type/version are immutable.
- **Landing / Discovery HTML** (when ``TEST_SUITE`` is false): ``GET /`` and ``GET /discovery``
  - **`PROJECT_TITLE`** / **`PROJECT_VERSION`** — Brand name and version
  - **`LANDING_TAGLINE`** / **`LANDING_DESCRIPTION`** — Hero copy (description optional)
  - **`LANDING_LOGO_URL`**, **`LANDING_PRIMARY_COLOR`**, **`LANDING_SECONDARY_COLOR`** — colours must be `#RGB` / `#RRGGBB` / `#RRGGBBAA` (else defaults); logo must be `http(s)` or a same-origin path (`/…`)
  - **`LANDING_PARTNER_URL`** / **`LANDING_PARTNER_LABEL`** — partner link only when URL is non-empty `http(s)`; other schemes are dropped
  - **`DISCOVERY_MAX_RECORDS`** — max CredentialRecord rows loaded for `/discovery` (default `1000`, newest first)
  - **`VIEW_UNSAFE_MODE`** — when ``false`` (default), bare ``GET /view`` redirects to ``/discovery`` and only same-origin credential / status-list / OCA URLs are accepted. When ``true``, bare ``/view`` shows a URL resolver form and may fetch **remote hosts**, but still only path-shaped URLs (``/credentials/{id}``, ``/status-lists/{id}``, ``/templates/{type}/{version}/oca.json``). Outbound GETs refuse redirects and block non-public resolved IPs (SSRF). Keep off in production.
  - **OCA without ``renderMethod``** — newly composed Conformity credentials omit ``renderMethod`` for UNTP playground validation. ``GET /view`` then loads the OCA bundle using Mongo ``CredentialRecord.type`` (publisher config name, e.g. ``BCMinesActPermitCredential``), not the VC ``type`` (``DigitalConformityCredential``). Discovery and same-origin credential URLs rely on that record; a VC alone (no record / no ``renderMethod``) will not resolve overlays.
- **MongoDB** — either a full URI or separate fields:
  - **`MONGO_URI`** — connection string (overrides host/port/user/password); database selection always uses **`MONGO_DB`**
  - or **`MONGO_HOST`**, **`MONGO_PORT`**, **`MONGO_USER`**, **`MONGO_PASSWORD`**, **`MONGO_DB`**
  - **`MONGO_AUTH_SOURCE`** — optional for split mode (e.g. `admin` on Railway/managed MongoDB)
- **`WEBVH_SERVER_URL`** — WebVH / DID web server base URL (e.g. `https://sandbox.bcvh.vonx.io`). Issuer DIDs from `configs/issuers.yaml` use this URL's hostname: alias `mines-act:chief-permitting-officer` becomes `did:web:{host}:mines-act:chief-permitting-officer`.
- **`PUBLISHER_WITNESS_ID`** — Witness `did:key` for Traction proof options (e.g. `did:key:z6Mk…`). The Ed25519 multikey is derived at runtime as **`PUBLISHER_WITNESS_MULTIKEY`**.

## Local demo seed

With the API running and Traction configured, publish sample Mines Act permits (including republications for Discovery iteration demos):

```bash
cd backend
uv run python scripts/seed_permits.py
# or: uv run python scripts/seed_permits.py --base-url http://127.0.0.1:8000
```

Uses admin ``X-API-Key`` (``TRACTION_API_KEY``). Payloads live in ``scripts/seed_data/mines_act_permits.json``.
