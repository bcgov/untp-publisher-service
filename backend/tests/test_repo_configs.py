"""Tests for issuer publication config loader."""

from app.repo_configs.loader import (
    load_credential_template,
    load_publication_config,
    load_publication_config_by_issuer,
)


def test_load_publication_config_by_credential_type():
    pub = load_publication_config("BCMinesActPermitCredential")
    assert pub["issuer"]["name"] == "Chief Permitting Officer"
    assert pub["credential"]["version"] == "v1.1"
    assert pub["credential"]["validation"]["permitNumberField"] == "permitNumber"


def test_load_credential_template_from_publication_config():
    template = load_credential_template("BCMinesActPermitCredential")
    assert template["name"] == "Mines Act Permit"
    assert template["version"] == "v1.1"
    assert "{{ options.cardinalityId }}" in template["credentialSubject"]["name"]


def test_credential_version_for_type():
    from app.repo_configs.loader import credential_version_for_type

    assert credential_version_for_type("BCMinesActPermitCredential") == "v1.1"


def test_load_publication_config_by_issuer():
    config = load_publication_config_by_issuer(
        "did:web:registry.digitaltrust.gov.bc.ca:mines-act:chief-permitting-officer"
    )
    assert len(config["credentials"]) == 1
    assert config["credentials"][0]["type"] == "BCMinesActPermitCredential"
