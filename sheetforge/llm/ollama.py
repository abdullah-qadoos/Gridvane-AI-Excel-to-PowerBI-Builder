"""Discovery is explicit and restricted to the local Ollama endpoint."""

import json
import urllib.error
import urllib.request


def discover_ollama():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://localhost:11434/api/tags", timeout=2) as response:
            data = json.loads(response.read(1_000_000))
        if not isinstance(data, dict) or not isinstance(data.get("models"), list):
            return {"available": False, "models": []}
        names = sorted(
            {
                item["name"]
                for item in data["models"]
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
        )
        return {"available": True, "models": names}
    except (OSError, ValueError, urllib.error.URLError):
        return {"available": False, "models": []}
