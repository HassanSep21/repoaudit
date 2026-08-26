from abc import ABC, abstractmethod
from typing import Optional

# Re-export built-in TimeoutError for convenience
TimeoutError = TimeoutError


class LLMProvider(ABC):
    """Abstract interface for LLM providers. Only Semantic Analysis uses this."""

    @abstractmethod
    def generate(self, prompt: str, *, max_tokens: int, timeout_s: int) -> str:
        """Generate a response from the LLM.

        Args:
            prompt: The full prompt to send
            max_tokens: Maximum tokens in response
            timeout_s: Request timeout in seconds

        Returns:
            The raw response text from the LLM

        Raises:
            TimeoutError: If the request times out
            RuntimeError: If the API returns an error or rate limits
        """
        ...


class GroqProvider(LLMProvider):
    """Groq API provider using llama-3.3-70b-versatile (configurable via GROQ_MODEL)."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        import os
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        self.model = model or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        self.base_url = "https://api.groq.com/openai/v1/chat/completions"

        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY not set")

    def generate(self, prompt: str, *, max_tokens: int, timeout_s: int) -> str:
        import httpx

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }

        try:
            with httpx.Client(timeout=timeout_s) as client:
                response = client.post(self.base_url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()

            if "choices" not in data or not data["choices"]:
                raise RuntimeError(f"Unexpected Groq response format: {data}")

            content = data["choices"][0]["message"]["content"]
            if not content:
                raise RuntimeError("Empty response from Groq")

            return content

        except httpx.TimeoutException:
            raise TimeoutError(f"Groq request timed out after {timeout_s}s")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise RuntimeError("Groq rate limit exceeded")
            raise RuntimeError(f"Groq API error: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            if isinstance(e, (TimeoutError, RuntimeError)):
                raise
            raise RuntimeError(f"Groq request failed: {e}")


class OllamaProvider(LLMProvider):
    """Optional local Ollama provider - not used by default."""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3"):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def generate(self, prompt: str, *, max_tokens: int, timeout_s: int) -> str:
        import httpx

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": 0.1},
        }

        try:
            with httpx.Client(timeout=timeout_s) as client:
                response = client.post(f"{self.base_url}/api/generate", json=payload)
                response.raise_for_status()
                data = response.json()

            content = data.get("response", "")
            if not content:
                raise RuntimeError("Empty response from Ollama")

            return content

        except httpx.TimeoutException:
            raise TimeoutError(f"Ollama request timed out after {timeout_s}s")
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Ollama API error: {e.response.status_code}")
        except Exception as e:
            if isinstance(e, TimeoutError):
                raise
            raise RuntimeError(f"Ollama request failed: {e}")


def get_provider() -> LLMProvider:
    """Factory to get the configured LLM provider. Defaults to Groq."""
    import os
    provider_type = os.getenv("LLM_PROVIDER", "groq").lower()

    if provider_type == "ollama":
        return OllamaProvider()
    return GroqProvider()