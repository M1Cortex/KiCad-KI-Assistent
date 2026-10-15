"""LLM backends: Ollama (local) and any OpenAI-compatible API.

Both backends speak JSON-RPC-free plain HTTP and are implemented on top of
``requests`` which ships with KiCad's Python, so the plugin has no hard
dependency beyond KiCad itself.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterator

try:  # pragma: no cover - requests ships with KiCad
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

__all__ = ["LLMConfig", "LLMResponse", "OllamaClient", "OpenAIClient", "LLMError", "build_client"]


class LLMError(RuntimeError):
    pass


@dataclass
class LLMConfig:
    """Configuration for the AI backend.

    Values are resolved from (in order of precedence):
    explicit arguments, environment variables, the plugin config file, defaults.
    """

    backend: str = "ollama"  # "ollama" | "openai" | "none"
    model: str = "qwen2.5-coder:14b"
    base_url: str = "http://localhost:11434"
    api_key: str = ""
    temperature: float = 0.1
    num_ctx: int = 16384
    max_tokens: int = 4096
    timeout: int = 600
    system_prompt: str = ""

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """Resolve settings from env vars, then the config file, then defaults.

        KiCad launches plugin actions as separate processes, so environment
        variables set in the user's shell do not reach the plugin. The config
        file is therefore the primary mechanism for interactive use.
        """
        cfg = cls()

        try:
            from .config import AIConfig

            stored = AIConfig.load()
        except Exception:  # noqa: BLE001 - config is optional
            stored = None

        if stored is not None:
            cfg.backend = stored.backend or cfg.backend
            cfg.model = stored.model or cfg.model
            cfg.base_url = stored.base_url or cfg.base_url
            cfg.api_key = stored.api_key or cfg.api_key
            cfg.temperature = stored.temperature
            cfg.max_tokens = stored.max_tokens
            cfg.timeout = stored.timeout

        cfg.backend = os.environ.get("KICAD_AI_BACKEND", cfg.backend).lower()
        if cfg.backend == "ollama":
            cfg.model = os.environ.get("KICAD_AI_MODEL", cfg.model)
            cfg.base_url = os.environ.get("OLLAMA_HOST", cfg.base_url)
            if "://" not in cfg.base_url:
                cfg.base_url = f"http://{cfg.base_url}"
            cfg.base_url = cfg.base_url.rstrip("/")
        else:
            cfg.model = os.environ.get("KICAD_AI_MODEL", cfg.model)
            cfg.base_url = os.environ.get("KICAD_AI_BASE_URL", cfg.base_url).rstrip("/")
            for var in ("OPENAI_API_KEY", "KICAD_AI_API_KEY"):
                if os.environ.get(var):
                    cfg.api_key = os.environ[var]
                    break
        return cfg


@dataclass
class LLMResponse:
    text: str
    model: str = ""
    backend: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_s: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


class _BaseClient:
    backend = "base"

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._session = None

    @property
    def session(self):
        if requests is None:
            raise LLMError("Python package 'requests' is not available")
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def available(self) -> tuple[bool, str]:
        raise NotImplementedError

    def models(self) -> list[str]:
        raise NotImplementedError

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        raise NotImplementedError

    def _post_json(self, url: str, payload: dict, headers: dict | None = None) -> dict:
        try:
            resp = self.session.post(
                url,
                json=payload,
                headers=headers or {},
                timeout=self.config.timeout,
            )
        except requests.exceptions.ConnectionError as exc:
            raise LLMError(f"Keine Verbindung zu {url}: {exc}") from exc
        except requests.exceptions.Timeout as exc:
            raise LLMError(f"Zeitüberschreitung bei {url}") from exc
        if resp.status_code >= 400:
            raise LLMError(self._explain_error(resp))
        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            raise LLMError(f"Ungültige Antwort von {url}: {resp.text[:200]}") from exc

    def _explain_error(self, resp) -> str:
        """Turn an HTTP error into an actionable message.

        A missing model is the most common cause and is easy to fix, so the
        available models are listed instead of showing the raw API error.
        """
        status = resp.status_code
        body = (resp.text or "")[:400]
        model = ""
        try:
            model = resp.request.json().get("model", "")
        except Exception:  # noqa: BLE001
            pass

        if status == 404 and "not found" in body.lower():
            lines = [f"Das Modell '{model}' ist auf dem Server nicht vorhanden."]
            available: list[str] = []
            try:
                available = self.models()
            except Exception:  # noqa: BLE001
                pass
            if available:
                lines.append("Verfügbar sind:")
                lines.extend(f"  - {name}" for name in available)
                lines.append("")
                lines.append(
                    "Modell umstellen mit:  python -m kicad_ai set-model <name>"
                )
            else:
                lines.append(f"Keine Modelle gefunden. Installiere eines mit:  ollama pull {model or 'qwen2.5-coder:14b'}")
            return "\n".join(lines)

        if status in (401, 403):
            return (
                f"Zugriff verweigert (HTTP {status}). Der API-Schlüssel fehlt "
                "oder ist ungültig."
            )
        if status == 400 and "context" in body.lower():
            return (
                "Der Kontext ist zu groß für das Modell. Verkleinere ihn mit "
                "--min-severity oder verwende ein Modell mit größerem Kontextfenster."
            )
        return f"HTTP {status} von {self.api}: {body}"


class OllamaClient(_BaseClient):
    """Local inference via Ollama.

    Beyond plain chatting this exposes the introspection the review needs:
    which models exist, how large their context window is, whether they can
    emit structured JSON, and which one suits the installed hardware best.
    """

    backend = "ollama"

    #: Preference order when several models could serve. Coding models handle
    #: schematic review better than general chat models because the task is
    #: largely about reading structured data and naming parts.
    MODEL_PREFERENCE = (
        "qwen2.5-coder",
        "qwen3-coder",
        "codestral",
        "deepseek-coder",
        "starcoder2",
        "qwen2.5",
        "qwen3",
        "llama3.1",
        "llama3.2",
        "mistral",
        "gemma3",
        "phi4",
    )

    @property
    def api(self) -> str:
        return self.config.base_url or "http://localhost:11434"

    # -- health ------------------------------------------------------------ #

    def version(self) -> str:
        try:
            return str(self.session.get(f"{self.api}/api/version", timeout=5).json().get("version", "?"))
        except Exception:  # noqa: BLE001
            return "?"

    def available(self) -> tuple[bool, str]:
        try:
            resp = self.session.get(f"{self.api}/api/tags", timeout=5)
            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code}"
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            if not models:
                return False, (
                    "Ollama läuft, hat aber keine Modelle installiert. "
                    "Lade eines herunter:  ollama pull qwen2.5-coder:7b"
                )
            return True, f"Ollama {self.version()}, {len(models)} Modell(e)"
        except Exception as exc:  # noqa: BLE001 - report any connectivity problem
            return False, f"Ollama nicht erreichbar ({type(exc).__name__}) auf {self.api}"

    def models(self) -> list[str]:
        resp = self.session.get(f"{self.api}/api/tags", timeout=10)
        resp.raise_for_status()
        return [m.get("name", "") for m in resp.json().get("models", [])]

    def model_details(self, name: str) -> dict:
        """Return size, family and quantisation of one model."""
        for entry in self.session.get(f"{self.api}/api/tags", timeout=10).json().get("models", []):
            if entry.get("name") == name or entry.get("model") == name:
                details = entry.get("details", {})
                return {
                    "name": name,
                    "size_bytes": entry.get("size", 0),
                    "family": details.get("family", ""),
                    "parameter_size": details.get("parameter_size", ""),
                    "quantization": details.get("quantization_level", ""),
                }
        return {"name": name}

    def show(self, name: str | None = None) -> dict:
        """Ask Ollama about a model: context window, capabilities, template."""
        model = name or self.config.model
        resp = self.session.post(
            f"{self.api}/api/show",
            json={"model": model},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        model_info = data.get("model_info", {}) or {}
        context = 0
        for key, value in model_info.items():
            if key.endswith(".context_length"):
                context = int(value)
                break
        capabilities = data.get("capabilities", []) or []
        return {
            "model": model,
            "context_length": context,
            "capabilities": capabilities,
            "parameter_size": data.get("details", {}).get("parameter_size", ""),
            "quantization": data.get("details", {}).get("quantization_level", ""),
        }

    def is_installed(self, name: str) -> bool:
        return name in self.models()

    # -- selection --------------------------------------------------------- #

    def recommend(self) -> str | None:
        """Pick the most suitable installed model, or None when none exists.

        Any of the known good model families beats an unknown one, and among
        equals the preference order decides. This keeps the plugin usable on a
        fresh machine without the user first having to read a model list.
        """
        installed = self.models()
        if not installed:
            return None

        def rank(name: str) -> tuple:
            lowered = name.lower()
            for index, family in enumerate(self.MODEL_PREFERENCE):
                if family in lowered:
                    return (index, len(name))
            return (len(self.MODEL_PREFERENCE), len(name))

        return sorted(installed, key=rank)[0]

    def context_limit(self) -> int:
        """Usable context window of the configured model, in tokens."""
        try:
            info = self.show()
        except Exception:  # noqa: BLE001
            return 4096
        return int(info.get("context_length") or 4096)

    # -- inference --------------------------------------------------------- #

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        progress: Any = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Send a chat request, streaming when possible.

        Streaming keeps long answers from looking like a hang: without it a
        big schematic context plus a slow local model can exceed any sensible
        client timeout.
        """
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": True,
            "format": kwargs.get("format", "json"),
            "options": {
                "temperature": self.config.temperature,
                "num_ctx": self.config.num_ctx,
                "num_predict": self.config.max_tokens,
            },
        }

        chunks: list[str] = []
        prompt_tokens = completion_tokens = 0
        model_name = self.config.model
        last_error: Exception | None = None

        for attempt in range(2):
            chunks.clear()
            prompt_tokens = completion_tokens = 0
            try:
                resp = self.session.post(
                    f"{self.api}/api/chat",
                    json=payload,
                    timeout=self.config.timeout,
                    stream=True,
                )
                if resp.status_code >= 400:
                    raise LLMError(self._explain_error(resp))

                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("error"):
                        raise LLMError(str(event["error"]))
                    piece = (event.get("message") or {}).get("content", "")
                    if piece:
                        chunks.append(piece)
                        if progress is not None:
                            progress(len(piece), "".join(chunks))
                    model_name = event.get("model", model_name)
                    prompt_tokens = event.get("prompt_eval_count", prompt_tokens)
                    completion_tokens = event.get("eval_count", completion_tokens)
                    if event.get("done"):
                        break
                break
            except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError) as exc:
                # A partial stream is common when a large model swaps memory;
                # one retry usually completes the answer.
                last_error = exc
                if attempt == 1 or not chunks:
                    raise LLMError(
                        f"Verbindung zu Ollama unterbrochen: {exc}"
                    ) from exc
            except LLMError:
                raise
        else:  # pragma: no cover - defensive
            raise LLMError(str(last_error) if last_error else "Unbekannter Ollama-Fehler")

        return LLMResponse(
            text="".join(chunks),
            model=model_name,
            backend=self.backend,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

class OpenAIClient(_BaseClient):
    """Any endpoint speaking the OpenAI ``/chat/completions`` API.

    Works with OpenAI itself, Azure OpenAI, LM Studio, vLLM, llama.cpp server,
    Groq, Together, OpenRouter and others.
    """

    backend = "openai"

    @property
    def api(self) -> str:
        base = self.config.base_url or "https://api.openai.com/v1"
        return base.rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def available(self) -> tuple[bool, str]:
        try:
            resp = self.session.get(f"{self.api}/models", headers=self._headers(), timeout=5)
            if resp.status_code in (200, 401, 403):
                return True, f"API erreichbar ({self.api})"
            return False, f"HTTP {resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            return False, f"API nicht erreichbar ({type(exc).__name__})"

    def models(self) -> list[str]:
        resp = self.session.get(f"{self.api}/models", headers=self._headers(), timeout=10)
        resp.raise_for_status()
        return [m.get("id", "") for m in resp.json().get("data", [])]

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "response_format": {"type": "json_object"},
        }
        data = self._post_json(f"{self.api}/chat/completions", payload, self._headers())
        choices = data.get("choices") or [{}]
        text = choices[0].get("message", {}).get("content", "")
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            model=data.get("model", self.config.model),
            backend=self.backend,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )


def build_client(config: LLMConfig) -> _BaseClient:
    """Return the client matching ``config.backend``."""
    backend = (config.backend or "none").lower()
    if backend == "ollama":
        return OllamaClient(config)
    if backend in ("openai", "api", "cloud"):
        config.backend = "openai"
        return OpenAIClient(config)
    raise LLMError(f"Unbekanntes Backend: {config.backend!r}")