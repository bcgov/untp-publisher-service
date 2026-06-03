"""Bundled credential-type presets (templateRef → DCC skeleton + publication contract)."""

from app.presets.loader import (
    build_template_from_preset,
    get_preset,
    list_preset_refs,
    load_oca_bundle,
    load_publication_example,
)

__all__ = [
    "build_template_from_preset",
    "get_preset",
    "list_preset_refs",
    "load_oca_bundle",
    "load_publication_example",
]
