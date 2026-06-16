"""
ollama_client.py — Local Ollama Integration
=============================================
Talks to Ollama running locally via HTTP.
Free, runs on the user's GPU/CPU.

Default model: qwen2.5:3b — small enough for a GTX 1650 (4GB VRAM)
              ~1.9 GB download
              ~8-15s per response on consumer GPU

Install: ollama pull qwen2.5:3b
Run:     ollama serve
"""

import os
import json
import logging
import requests
from typing import Optional

log = logging.getLogger("nexus.ollama")

OLLAMA_HOST    = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60"))   # seconds


class OllamaClient:
    """
    Thin HTTP wrapper around the Ollama server.

    Raises Exception on failure so the AIRouter can fall back to a paid model.
    """

    def __init__(self, host: str = None, timeout: int = None):
        self.host    = host    or OLLAMA_HOST
        self.timeout = timeout or OLLAMA_TIMEOUT

    def is_available(self) -> bool:
        """Quick health check — used by router to decide on fallback."""
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list:
        """Get list of installed local models."""
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=5)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        except Exception as e:
            log.warning(f"Could not list Ollama models: {e}")
            return []

    def generate(
        self,
        model:  str,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.7,
    ) -> str:
        """
        Generate a single response. No streaming — returns full text.

        On error, raises so AIRouter falls back to a paid model.
        """
        payload = {
            "model":  model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature":     temperature,
                "num_predict":     1024,
            },
        }
        if system:
            payload["system"] = system

        try:
            r = requests.post(
                f"{self.host}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()
            return (data.get("response") or "").strip()
        except requests.Timeout:
            log.error(f"Ollama timeout after {self.timeout}s for model {model}")
            raise RuntimeError(f"Ollama timed out ({self.timeout}s)")
        except requests.ConnectionError:
            log.error(f"Ollama not running at {self.host}")
            raise RuntimeError(f"Ollama not reachable at {self.host} — run 'ollama serve'")
        except Exception as e:
            log.error(f"Ollama error: {e}")
            raise

    def embed(self, model: str, text: str) -> list:
        """
        Get vector embedding for text. Used later for semantic search.
        Requires an embedding-capable model (nomic-embed-text, etc).
        """
        try:
            r = requests.post(
                f"{self.host}/api/embeddings",
                json={"model": model, "prompt": text},
                timeout=self.timeout,
            )
            r.raise_for_status()
            return r.json().get("embedding", [])
        except Exception as e:
            log.error(f"Ollama embed error: {e}")
            raise
        