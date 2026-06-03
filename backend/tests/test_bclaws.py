"""Tests for BC Laws CiviX client."""

from app.plugins import bclaws

SAMPLE_LETTER_D = """<?xml version="1.0"?>
<root>
  <dir>
    <CIVIX_DOCUMENT_TITLE>Debtor Assistance Act [RSBC 1996] c. 93</CIVIX_DOCUMENT_TITLE>
    <CIVIX_DOCUMENT_ID>96093</CIVIX_DOCUMENT_ID>
    <CIVIX_INDEX_ID>statreg</CIVIX_INDEX_ID>
    <CIVIX_DOCUMENT_TYPE>dir</CIVIX_DOCUMENT_TYPE>
  </dir>
  <dir>
    <CIVIX_DOCUMENT_TITLE>Debt Collection Act [RSBC 1996] c. 92</CIVIX_DOCUMENT_TITLE>
    <CIVIX_DOCUMENT_ID>96092rep</CIVIX_DOCUMENT_ID>
    <CIVIX_INDEX_ID>statreg</CIVIX_INDEX_ID>
    <CIVIX_DOCUMENT_TYPE>dir</CIVIX_DOCUMENT_TYPE>
    <CIVIX_DOCUMENT_STATUS>Repealed</CIVIX_DOCUMENT_STATUS>
  </dir>
</root>"""


def test_parse_content_listing():
    entries = bclaws._parse_content_listing(SAMPLE_LETTER_D)
    assert len(entries) == 2
    assert entries[0]["title"].startswith("Debtor Assistance")
    assert entries[1]["status"] == "Repealed"


def test_act_name_from_title():
    assert (
        bclaws._act_name_from_title("Petroleum and Natural Gas Act [RSBC 1996] c. 361")
        == "Petroleum and Natural Gas Act"
    )


def test_document_url():
    url = bclaws.document_url("96361_01")
    assert url.endswith("/civix/document/id/complete/statreg/96361_01")


def test_resolve_legal_act_from_scope(monkeypatch):
    def fake_list_public_acts(*, q=None, **kwargs):
        assert q == "Mines Act"
        return {
            "acts": [
                {
                    "name": "Mines Act",
                    "title": "Mines Act [RSBC 1996] c. 293",
                    "documentId": "96293_01",
                    "id": bclaws.document_url("96293_01"),
                }
            ]
        }

    def fake_get_act_metadata(document_id):
        assert document_id == "96293_01"
        return {
            "id": bclaws.document_url(document_id),
            "name": "Mines Act",
            "effectiveDate": "1996-01-01",
            "documentId": document_id,
        }

    monkeypatch.setattr(bclaws, "list_public_acts", fake_list_public_acts)
    monkeypatch.setattr(bclaws, "get_act_metadata", fake_get_act_metadata)

    resolved = bclaws.resolve_legal_act_from_scope("Mines Act")
    assert resolved["name"] == "Mines Act"
    assert "96293_01" in resolved["id"]
    assert resolved["scope"] == "Mines Act"


def test_best_act_match_from_scope_exact():
    acts = [
        {"name": "Petroleum and Natural Gas Act", "id": "https://example/a"},
        {"name": "Mines Act", "id": "https://example/b"},
    ]
    match = bclaws.best_act_match_from_scope(acts, "Mines Act")
    assert match["name"] == "Mines Act"


def test_list_public_acts_filters(monkeypatch):
    def fake_fetch(*parts):
        if parts == ():
            return [{"title": "-- D --", "folderId": "1421132707", "type": "dir"}]
        if parts == ("1421132707",):
            return bclaws._parse_content_listing(SAMPLE_LETTER_D)
        return []

    monkeypatch.setattr(bclaws, "_fetch_content", fake_fetch)
    bclaws._catalog_cache.clear()

    result = bclaws.list_public_acts(letter="D", include_repealed=False)
    assert result["total"] == 1
    assert result["acts"][0]["name"] == "Debtor Assistance Act"
    assert "96093_01" in result["acts"][0]["documentId"]
