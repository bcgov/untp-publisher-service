from __future__ import annotations

from pathlib import Path
import logging
import os
from logging import Logger
from urllib.parse import urlparse

from pydantic import Field, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from witness import did_key_to_multikey

basedir = os.path.abspath(os.path.dirname(__file__))


class Settings(BaseSettings):
    """
    Configuration from environment and optional ``.env`` (same directory as this file).

    String settings default to local-development placeholders so ``import app`` works
    without a populated ``.env``. Override everything real deployments need.
    """

    model_config = SettingsConfigDict(
        env_file=os.path.join(basedir, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        arbitrary_types_allowed=True,
    )

    PROJECT_TITLE: str = "UNTP Publisher"
    PROJECT_VERSION: str = "v0"

    #: Landing / discovery HTML chrome (ignored in TEST_SUITE mode).
    LANDING_TAGLINE: str = Field(
        default="Publish UNTP-aligned verifiable credentials through BC Traction."
    )
    LANDING_DESCRIPTION: str = Field(default="")
    LANDING_LOGO_URL: str = Field(
        default="https://mines.nrs.gov.bc.ca/assets/images/bcgov-mineinfo-horiz-LG.png"
    )
    LANDING_PRIMARY_COLOR: str = Field(default="#013366")
    LANDING_SECONDARY_COLOR: str = Field(default="#FCBA19")
    #: Partner header link — rendered only when URL is non-empty.
    LANDING_PARTNER_URL: str = Field(default="")
    LANDING_PARTNER_LABEL: str = Field(default="Partner")
    #: Cap CredentialRecord rows loaded for ``GET /discovery`` (newest first).
    DISCOVERY_MAX_RECORDS: int = Field(default=1000, ge=1, le=10000)
    #: When ``True``, ``/view`` may fetch remote http(s) credential / status-list /
    #: OCA URLs (SSRF risk). Keep ``False`` in production.
    VIEW_UNSAFE_MODE: bool = Field(default=False)

    #: When ``True``, the app exposes only ``/server/status`` and ``/test-suite/*`` (no auth,
    #: credentials, or publisher API). Use for validator CI / isolated test deployments.
    TEST_SUITE: bool = Field(default=False)

    LOG_LEVEL: int = logging.INFO
    LOG_FORMAT: str = (
        "%(asctime)s | %(levelname)-8s | %(module)s:%(funcName)s:%(lineno)d | %(message)s"
    )
    LOGGER: Logger = Field(default_factory=lambda: logging.getLogger(__name__))

    PUBLISHER_DOMAIN: str = Field(default="http://localhost")
    #: When ``True``, OCA ``renderMethod`` includes ``digestMultibase`` of the
    #: current ``oca.json``. Prefer ``False`` unless the OCA URL bytes are
    #: immutable for that credential version (digest pins break if the file changes).
    OCA_DIGEST: bool = Field(default=False)
    TRACTION_API_URL: str = Field(default="http://localhost")
    TRACTION_API_KEY: str = Field(default="dev-local")
    TRACTION_TENANT_ID: str = Field(default="dev-local")

    #: WebVH / DID web server base URL (e.g. ``https://sandbox.bcvh.vonx.io``).
    #: Issuer DIDs from ``issuers.yaml`` use this URL's hostname:
    #: ``did:web:{host}:{alias}``.
    WEBVH_SERVER_URL: str = Field(default="http://localhost")
    #: Witness ``did:key`` (method id must be an Ed25519 multikey).
    PUBLISHER_WITNESS_ID: str = Field(default="")

    @computed_field
    @property
    def PUBLISHER_WITNESS_MULTIKEY(self) -> str:
        if not self.PUBLISHER_WITNESS_ID:
            return ""
        return did_key_to_multikey(self.PUBLISHER_WITNESS_ID)

    def publisher_domain(self) -> str:
        """Hostname for issuer ``did:web`` IDs (from ``WEBVH_SERVER_URL``)."""
        raw = (self.WEBVH_SERVER_URL or "").strip()
        if not raw:
            return ""
        if "://" not in raw:
            raw = f"https://{raw}"
        return (urlparse(raw).hostname or "").strip()

    SECRET_KEY: str = Field(default="dev-local")
    JWT_SECRET: str = Field(default="dev-local")
    JWT_ALGORITHM: str = "HS256"

    #: Full connection string (``mongodb://`` or ``mongodb+srv://``). When set, overrides
    #: ``MONGO_HOST`` / ``MONGO_PORT`` / ``MONGO_USER`` / ``MONGO_PASSWORD``.
    #: Convenience for pasting a managed-Mongo connection string; database name is always
    #: ``MONGO_DB`` (URI path is ignored for selection).
    MONGO_URI: str = Field(default="")

    MONGO_HOST: str = Field(default="localhost")
    MONGO_PORT: str = Field(default="27017")
    MONGO_USER: str = Field(default="dev")
    MONGO_PASSWORD: str = Field(default="dev")
    MONGO_DB: str = Field(default="dev")
    #: Auth database for split-variable mode only (e.g. ``admin`` on managed MongoDB).
    MONGO_AUTH_SOURCE: str = Field(default="")

    #: Root directory for ``configs/`` (``issuers.yaml``, ``credentials/{type}/{version}/``).
    #: Defaults to ``<repo>/configs``. In container images use ``/config``.
    CONFIG_ROOT: str = Field(default="")

    @model_validator(mode="after")
    def _validate_mongo_settings(self) -> Settings:
        if self.MONGO_DB.startswith(("mongodb://", "mongodb+srv://")):
            raise ValueError(
                "MONGO_DB must be the database name only (e.g. untp-publisher). "
                "Put the full connection string in MONGO_URI instead."
            )
        return self

    @model_validator(mode="after")
    def _configure_logging(self) -> Settings:
        logging.basicConfig(level=self.LOG_LEVEL, format=self.LOG_FORMAT)
        return self


settings = Settings()


def repo_root() -> Path:
    return Path(basedir).parent


def config_root() -> Path:
    if settings.CONFIG_ROOT.strip():
        return Path(settings.CONFIG_ROOT)
    return repo_root() / "configs"
