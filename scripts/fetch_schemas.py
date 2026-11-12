"""Maintainer-only download of pinned official JSON schemas and their dependencies."""

import hashlib
import json
from pathlib import Path
from urllib.parse import urldefrag, urljoin
from urllib.request import urlopen

BASE = "https://developer.microsoft.com/json-schemas/fabric/"
ROOT = Path(__file__).resolve().parents[1] / "sheetforge" / "schemas"
ROOTS = [
    "pbip/pbipProperties/1.0.0/schema.json",
    "item/semanticModel/definitionProperties/1.0.0/schema.json",
    "item/report/definitionProperties/2.0.0/schema.json",
    "item/report/definition/report/3.0.0/schema.json",
    "item/report/definition/page/2.0.0/schema.json",
    "item/report/definition/pagesMetadata/1.0.0/schema.json",
    "item/report/definition/versionMetadata/1.0.0/schema.json",
    "item/report/definition/visualContainer/2.4.0/schema.json",
]


def references(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "$ref" and isinstance(item, str) and not item.startswith("#"):
                yield item
            else:
                yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def main():
    queue = [BASE + path for path in ROOTS]
    manifest = {}
    while queue:
        url = urldefrag(queue.pop(0))[0]
        if url in manifest:
            continue
        if not url.startswith(BASE):
            raise ValueError("Unexpected schema host: " + url)
        fetch_url = url.replace("/schema.embedded.json", "/schema-embedded.json")
        with urlopen(fetch_url, timeout=30) as response:
            content = response.read()
        schema = json.loads(content)
        relative = "microsoft/" + url.removeprefix(BASE)
        destination = ROOT / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(json.dumps(schema, ensure_ascii=False, indent=2).encode("utf-8"))
        record = {
            "file": relative,
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "source": fetch_url,
        }
        manifest[url] = record
        if schema.get("$id"):
            manifest[schema["$id"]] = record
        queue.extend(urljoin(url, ref) for ref in references(schema))
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Bundled {len({item['file'] for item in manifest.values()})} official schema files.")


if __name__ == "__main__":
    main()
