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
Holder and permit ids are resolved from `data` via `issuers.yaml` pointers.

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
- **Landing / branding** (used by `GET /`; OpenAPI title uses `PROJECT_TITLE`):
  - **`PROJECT_TITLE`** — Brand name (default `UNTP Publisher`)
  - **`PROJECT_VERSION`** — Shown on the landing page and OpenAPI
  - **`LANDING_TAGLINE`** — One sentence under the title
  - **`LANDING_DESCRIPTION`** — Optional longer line (omitted when empty)
  - **`LANDING_LOGO_URL`** — Logo URL (default `/static/logo.jpg`)
  - **`LANDING_PRIMARY_COLOR`** — Primary accent (default `#003366`, BC navy / Mine Information header)
  - **`LANDING_SECONDARY_COLOR`** — Secondary accent (default `#FCBA19`, gold)
  - **`LANDING_PARTNER_URL`** / **`LANDING_PARTNER_LABEL`** — Partner chrome link (default [BC Mine Information](https://mines.nrs.gov.bc.ca/))
- **MongoDB** — either a full URI or separate fields:
  - **`MONGO_URI`** — connection string (overrides host/port/user/password); database selection always uses **`MONGO_DB`**
  - or **`MONGO_HOST`**, **`MONGO_PORT`**, **`MONGO_USER`**, **`MONGO_PASSWORD`**, **`MONGO_DB`**
  - **`MONGO_AUTH_SOURCE`** — optional for split mode (e.g. `admin` on Railway/managed MongoDB)
- **`WEBVH_SERVER_URL`** — WebVH / DID web server base URL (e.g. `https://sandbox.bcvh.vonx.io`). Issuer DIDs from `configs/issuers.yaml` use this URL's hostname: alias `mines-act:chief-permitting-officer` becomes `did:web:{host}:mines-act:chief-permitting-officer`.
- **`PUBLISHER_WITNESS_ID`** — Witness `did:key` for Traction proof options (e.g. `did:key:z6Mk…`). The Ed25519 multikey is derived at runtime as **`PUBLISHER_WITNESS_MULTIKEY`**.
