"""Tests for issuer publication config loader."""

from app.repo_configs.loader import (
    load_credential_template,
    load_credential_template_source,
    load_oca_bundle,
    load_publication_config,
    load_publication_config_by_issuer,
    load_sample_issued_credential_optional,
    load_sample_publication_payload,
    oca_bundle_path,
    sample_publication_payload_path,
    sample_set_dir,
)


def test_load_publication_config_by_credential_type():
    pub = load_publication_config("BCMinesActPermitCredential")
    assert pub["issuer"]["name"] == "Chief Permitting Officer"
    assert pub["credential"]["version"] == "v1.1"


def test_load_credential_template_from_publication_config():
    source = load_credential_template_source("BCMinesActPermitCredential")
    assert "name: Mines Act Permit" in source
    assert "{{ permitNumber }}" in source
    template = load_credential_template("BCMinesActPermitCredential")
    assert template["name"] == "Mines Act Permit"
    assert template["version"] == "v1.1"


def test_credential_version_for_type():
    from app.repo_configs.loader import credential_version_for_type

    assert credential_version_for_type("BCMinesActPermitCredential") == "v1.1"


def test_load_publication_config_by_issuer():
    config = load_publication_config_by_issuer(
        "did:web:registry.digitaltrust.gov.bc.ca:mines-act:chief-permitting-officer"
    )
    assert len(config["credentials"]) == 1
    assert config["credentials"][0]["type"] == "BCMinesActPermitCredential"


def test_inferred_sample_paths():
    assert sample_set_dir("BCMinesActPermitCredential").name == "BCMinesActPermitCredential.v1.1"
    assert sample_publication_payload_path("BCMinesActPermitCredential").name == "publication-payload.json"


def test_load_sample_publication_payload():
    payload = load_sample_publication_payload("BCMinesActPermitCredential")
    assert payload["options"]["cardinalityId"] == "Q-20"
    assert payload["options"]["additionalData"]["assessedFacility"]


def test_load_sample_issued_credential():
    issued = load_sample_issued_credential_optional("BCMinesActPermitCredential")
    assert issued is not None
    assessment = issued["credentialSubject"]["conformityAssessment"][0]
    assert assessment["assessedFacility"]
    assert assessment["assessedProduct"]


def test_inferred_oca_bundle_path():
    path = oca_bundle_path("BCMinesActPermitCredential")
    assert path.name == "BCMinesActPermitCredential.v1.1.json"


def test_load_oca_bundle():
    bundle = load_oca_bundle("BCMinesActPermitCredential")
    assert bundle["type"] == "spec/capture_base/1.0"
    overlays = bundle["overlays"]
    overlay_types = {o["type"] for o in overlays}
    assert "overlay/mapping/2.0.0" not in overlay_types
    label_overlays = [o for o in overlays if o["type"] == "spec/overlays/label/1.0"]
    info_overlays = [o for o in overlays if o["type"] == "spec/overlays/information/1.0"]
    assert {o["language"] for o in label_overlays} == {"en", "fr"}
    assert {o["language"] for o in info_overlays} == {"en", "fr"}
    en_labels = next(o for o in label_overlays if o["language"] == "en")
    assert (
        en_labels["attribute_labels"][
            "/credentialSubject/conformityAssessment/0/registeredId"
        ]
        == "Permit number"
    )
    assert len(en_labels["attribute_labels"]) == len(bundle["attributes"])
