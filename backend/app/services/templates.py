"""Render Jinja credential ``template.yaml`` files (publish + provisioning stubs).

Threat model
------------
* **Trusted:** ``configs/credentials/.../template.yaml`` checked into this repo
  (or otherwise controlled by operators). Authors can use full Jinja here.
* **Untrusted:** publish request ``data`` (context values only). That is *data*,
  not template source — do not eval client strings as Jinja.

Footguns to avoid
-----------------
* Do **not** let untrusted users author or upload ``template.yaml``. A Jinja
  sandbox reduces risk but has had breakouts historically; treat template
  authoring as a **capability boundary** (same trust as deploying app code).
* Do **not** pass user-controlled strings into ``Environment.from_string`` /
  ``render`` as the *template* text. Only render fixed templates with
  untrusted values in the context dict.
* Do **not** register filters/globals that reach the network, filesystem, or
  subprocesses. Keep helpers pure (``required``, ``isodate``, ``toslug``, ``tojson``).
* Do **not** put reserved Jinja names in publish ``data`` keys (``fail``,
  ``namespace``, ``range``, …) — rejected by ``_jinja_context_from_data``.
* Keep Jinja patched (sandbox fixes land in patch releases).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

import yaml
from fastapi import HTTPException
from jinja2 import TemplateSyntaxError, Undefined, UndefinedError
from jinja2.sandbox import SandboxedEnvironment


class _ChainableStrictUndefined(Undefined):
    """Undefined that chains attribute access so ``foo.bar | required`` can run.

    Stock StrictUndefined raises before the filter sees a missing parent/attr.
    Printing an undefined into the rendered YAML still fails.
    """

    def _fail_with_undefined_error(self, *args, **kwargs):
        raise self._undefined_exception(self._undefined_message)

    __str__ = __html__ = _fail_with_undefined_error  # type: ignore[assignment]
    __iter__ = __len__ = __bool__ = __int__ = __float__ = _fail_with_undefined_error  # type: ignore[assignment]
    __complex__ = __hash__ = __eq__ = __ne__ = _fail_with_undefined_error  # type: ignore[assignment]
    __lt__ = __le__ = __gt__ = __ge__ = _fail_with_undefined_error  # type: ignore[assignment]

    def __getattr__(self, name: str):
        if name[:2] == "__":
            raise AttributeError(name)
        return self.__class__(hint=self._undefined_hint, obj=self._undefined_obj, name=name)

    def __getitem__(self, _key):
        return self.__class__(
            hint=self._undefined_hint, obj=self._undefined_obj, name=self._undefined_name
        )


# SandboxedEnvironment: block unsafe Jinja constructs in credential templates.
# Custom undefined: allow ``| required`` on missing parents (see class docstring).
_JINJA = SandboxedEnvironment(autoescape=False, undefined=_ChainableStrictUndefined)
_JINJA.filters["tojson"] = json.dumps


def toslug(name: str) -> str:
    """Lowercase slug for ids (non-alnum runs become ``-``)."""
    value = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return value or "product"


_JINJA.filters["toslug"] = toslug


def _fail(message: str) -> str:
    """Raise a client error from a credential template (Jinja ``fail`` helper)."""
    raise HTTPException(status_code=400, detail=str(message))


def required(value: Any, label: str = "value") -> Any:
    """Return ``value`` if defined and non-empty, else 400."""
    if isinstance(value, Undefined) or value is None:
        _fail(f"{label} is required")
    if isinstance(value, str) and not value.strip():
        _fail(f"{label} is required")
    return value


def isodate(value: Any, label: str = "value") -> str:
    """Require a non-empty ISO 8601 date (``YYYY-MM-DD``) or datetime; return as-is."""
    text = str(required(value, label=label)).strip()
    try:
        if len(text) == 10:
            date.fromisoformat(text)
        else:
            datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        _fail(f"{label} must be an ISO 8601 date or datetime")
    return text


_JINJA.globals["fail"] = _fail
_JINJA.filters["required"] = required
_JINJA.filters["isodate"] = isodate

# Top-level ``data`` keys that would shadow Jinja / render helpers.
_RESERVED_CONTEXT_KEYS = frozenset(
    {
        "fail",
        "range",
        "dict",
        "namespace",
        "cycler",
        "joiner",
        "lipsum",
    }
)


def _jinja_context_from_data(data: dict[str, Any]) -> dict[str, Any]:
    """Use publish ``data`` as the Jinja context (top-level permit, mine, …)."""
    clash = _RESERVED_CONTEXT_KEYS.intersection(data)
    if clash:
        raise HTTPException(
            status_code=400,
            detail=(
                "data keys conflict with template engine names: "
                + ", ".join(sorted(clash))
            ),
        )
    return dict(data)


def template_stub_context() -> dict[str, Any]:
    """Minimal context to render a template for structure inspection."""
    return _jinja_context_from_data(
        {
            "permit": {
                "issuanceDate": "1999-01-01",
                "identifier": "STUB",
            },
            "permittee": {
                "name": "Stub Organization",
                "identifier": "STUB",
            },
            "mine": {
                "name": "Stub Site",
                "identifier": "0000000",
                "locationInformation": "https://plus.codes/EXAMPLE+CODE",
            },
            "commodities": [],
        }
    )


def render_template_text(template: str, context: dict[str, Any]) -> str:
    if not template:
        return template
    if "{{" not in template and "{%" not in template and "{#" not in template:
        return template
    try:
        return _JINJA.from_string(template).render(**context)
    except HTTPException:
        raise
    except TemplateSyntaxError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Invalid publication Jinja template: {exc}",
        ) from exc
    except UndefinedError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Publication Jinja template references undefined variable: {exc}",
        ) from exc


def publication_template_context(*, options: dict[str, Any]) -> dict[str, Any]:
    """Jinja context is the request ``data`` object (keys at top level)."""
    data = options.get("data") if isinstance(options.get("data"), dict) else {}
    return _jinja_context_from_data(data)


def render_template_yaml(template_source: str, context: dict[str, Any]) -> dict[str, Any]:
    """Render a YAML template file with Jinja, then parse the result."""
    rendered = render_template_text(template_source, context)
    try:
        data = yaml.safe_load(rendered) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Credential template did not render valid YAML: {exc}",
        ) from exc
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=500,
            detail="Credential template must render to a YAML mapping",
        )
    return data


def materialize_credential_document(
    template_source: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Render ``template.yaml`` into a VC-shaped dict (``@context``, assessment array, …)."""
    document = render_template_yaml(template_source, context)

    if "context" in document and "@context" not in document:
        document["@context"] = document.pop("context")

    subject = document.setdefault("credentialSubject", {})
    if not isinstance(subject, dict):
        raise HTTPException(
            status_code=500,
            detail="credentialSubject must be a mapping after template render",
        )

    assessment = subject.get("conformityAssessment")
    if isinstance(assessment, dict):
        subject["conformityAssessment"] = [assessment]
    elif not isinstance(assessment, list) or not assessment:
        raise HTTPException(
            status_code=500,
            detail="credentialSubject.conformityAssessment must be a non-empty list after render",
        )

    return document


def build_registration_template(
    *,
    credential_type: str,
    issuer: dict[str, Any],
) -> dict[str, Any]:
    """Stub-render ``template.yaml`` and attach the configured issuer (provisioning)."""
    from app.repo_configs.loader import load_credential_template_source

    source = load_credential_template_source(credential_type)
    document = materialize_credential_document(source, template_stub_context())
    document.pop("id", None)
    document.pop("validFrom", None)
    document.pop("validUntil", None)
    document.pop("proof", None)

    document["issuer"] = {
        "type": ["CredentialIssuer"],
        "id": issuer["id"],
        "name": issuer["name"],
    }

    subject = document.get("credentialSubject") or {}
    if not (subject.get("referenceScheme") or {}).get("id"):
        raise HTTPException(
            status_code=500,
            detail=(
                f"Template for {credential_type!r} is missing "
                "credentialSubject.referenceScheme; set it in template.yaml"
            ),
        )
    return document
