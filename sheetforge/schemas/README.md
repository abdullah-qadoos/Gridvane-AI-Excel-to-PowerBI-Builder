# Bundled validation resources

`manifest.json` pins official Microsoft PBIP, PBIR, and semantic-model definition schemas, their URLs, and SHA-256 hashes. `scripts/fetch_schemas.py` is an explicit maintainer operation. Generating or validating a project never downloads schemas.

Source: https://github.com/microsoft/json-schemas/tree/main/fabric

TMDL is a text language, not JSON; no JSON schema can validate its grammar. The `tmdl` reference describes the emitted subset. PBIR and definition JSON are checked against the official bundled schemas. Desktop/TOM parsing remains the step 3 review gate, rather than being incorrectly described as JSON validation of TMDL.
