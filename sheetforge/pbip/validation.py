"""Offline schema validation. Missing or tampered schema resources fail explicitly."""

import hashlib
import json
from importlib.resources import files


def validate_documents(documents):
    try:
        from jsonschema import Draft7Validator
        from referencing import Registry, Resource
        from referencing.jsonschema import DRAFT7
    except ImportError:
        raise RuntimeError(
            "Install sheetforge[validation] to validate generated PBIR files."
        ) from None
    root = files("sheetforge").joinpath("schemas")
    manifest = json.loads(root.joinpath("manifest.json").read_text(encoding="utf-8"))
    registry = Registry()
    for url, item in manifest.items():
        content = root.joinpath(item["file"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError("Bundled schema integrity check failed: " + item["file"])
        schema = json.loads(content)
        registry = registry.with_resource(
            url, Resource.from_contents(schema, default_specification=DRAFT7)
        )
    validated = []
    for path, document in documents.items():
        if not isinstance(document, dict) or "$schema" not in document:
            continue
        url = document["$schema"]
        if url not in manifest:
            raise ValueError("No bundled schema for " + url)
        schema = registry.contents(url)
        errors = sorted(
            Draft7Validator(schema, registry=registry).iter_errors(document),
            key=lambda error: str(error.path),
        )
        if errors:
            details = "; ".join(error.message for error in errors[:5])
            raise ValueError(f"{path}: {details}")
        validated.append(path)
    return validated
