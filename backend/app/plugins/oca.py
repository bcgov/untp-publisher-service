from jsonpath_ng import jsonpath, parse
from fastapi.templating import Jinja2Templates
import base64
from blake3 import blake3
import canonicaljson
import re


class OCAProcessorError(Exception):
    """Generic OCAProcessor Error."""


class OCAProcessor:
    def __init__(self):
        self.dummy_string = "#" * 44
        self.templates = Jinja2Templates(directory="app/templates")

    def load_template(self, name):
        with open(f"app/static/templates/{name}.html", "r") as f:
            template = f.read()
        return template

    def generate_said(self, value):
        # https://datatracker.ietf.org/doc/html/draft-ssmith-said#name-generation-and-verification
        # https://trustoverip.github.io/tswg-cesr-specification/#text-coding-scheme-design
        return "E" + (
            base64.urlsafe_b64encode(
                bytes([0]) + blake3(canonicaljson.encode_canonical_json(value)).digest()
            ).decode()
        ).lstrip("A")

    def secure_bundle(self, bundle):
        capture_base_said = ""
        bundle["digest"] = capture_base_said
        for idx, overlay in enumerate(bundle["overlays"]):
            bundle["overlays"][idx]["capture_base"] = capture_base_said
            overlay_said = ""
            bundle["overlays"][idx]["digest"] = overlay_said
        secured_bundle = bundle
        return secured_bundle

    def create_bundle(self, credential_registration, credential_template):
        capture_base = {
            "type": "spec/capture_base/1.0",
            "attributes": {},
            "flagged_attributes": [],
            "digest": self.dummy_string,
        }
        labels = {
            "type": "spec/overlays/label/1.0",
            "lang": "en",
            "attribute_labels": {},
        }
        information = {
            "type": "spec/overlays/information/1.0",
            "lang": "en",
            "attribute_information": {},
        }
        meta = {
            "type": "spec/overlays/meta/1.0",
            "language": "en",
            "issuer": credential_template["issuer"]["name"],
            "name": credential_template["name"],
            # "description": credential_template['description'],
        }

        branding = {
            "type": "aries/overlays/branding/1.0",
            "primary_attribute": "entityId",
            "secondary_attribute": "cardinalityId",
            "primary_background_color": "#003366",
            "secondary_background_color": "#00264D",
            "logo": "https://avatars.githubusercontent.com/u/916280",
        }
        paths = {"type": "vc/overlays/path/1.0", "attribute_paths": {}}
        clusters = {
            "type": "vc/overlays/cluster/1.0",
            "lang": "en",
            "attribute_clusters": {},
        }
        attributes = (
            credential_registration.get("corePaths")
            or credential_registration.get("core_paths")
            or {}
        ) | (
            credential_registration.get("subjectPaths")
            or credential_registration.get("subject_paths")
            or {}
        )
        for attribute in attributes:
            capture_base["attributes"][attribute] = "Text"
            labels["attribute_labels"][attribute] = " ".join(
                re.findall("[A-Z][^A-Z]*", attribute)
            ).upper()
            paths["attribute_paths"][attribute] = attributes[attribute]

        overlays = [
            labels,
            # information,
            meta,
            branding,
            paths,
            # clusters,
        ]

        capture_base["digest"] = self.generate_said(capture_base)
        for idx, overlay in enumerate(overlays):
            overlays[idx]["capture_base"] = capture_base["digest"]
            overlays[idx]["digest"] = self.dummy_string
            overlays[idx]["digest"] = self.generate_said(overlays[idx])

        bundle = capture_base | {"overlays": overlays}
        return bundle

    def get_overlay(self, bundle, overlay_type):
        return next(
            (
                overlay
                for overlay in bundle["overlays"]
                if overlay["type"] == overlay_type
            ),
            None,
        )

    def render(self, document, bundle):
        pass

    def create_context(self, document, bundle):
        information_overlay = self.get_overlay(bundle, "spec/overlays/information/1.0")
        labels_overlay = self.get_overlay(bundle, "spec/overlays/label/1.0")
        paths_overlay = self.get_overlay(bundle, "vc/overlays/paths/1.0")
        render_overlay = self.get_overlay(bundle, "vc/overlays/render/1.0")
        branding_overlay = self.get_overlay(bundle, "aries/overlays/branding/1.0")
        meta_overlay = self.get_overlay(bundle, "spec/overlays/meta/1.0")

        values = {}
        for attribute in paths_overlay["attribute_paths"]:
            jsonpath_expr = parse(paths_overlay["attribute_paths"][attribute])
            values[attribute] = [match.value for match in jsonpath_expr.find(document)][
                0
            ]
        return {
            "values": values,
            "labels": labels_overlay["attribute_labels"],
            "descriptions": information_overlay["attribute_information"],
            "groupings": render_overlay["attribute_groupings"],
            "meta": meta_overlay,
            "branding": branding_overlay,
        }
