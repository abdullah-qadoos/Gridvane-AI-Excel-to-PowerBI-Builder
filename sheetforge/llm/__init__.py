from .client import CompletionUnavailable, complete, configure, test_connection
from .config import LLMConfig, public_config, store_api_key
from .ollama import discover_ollama

__all__ = [
    "CompletionUnavailable",
    "LLMConfig",
    "complete",
    "configure",
    "discover_ollama",
    "public_config",
    "store_api_key",
    "test_connection",
]
