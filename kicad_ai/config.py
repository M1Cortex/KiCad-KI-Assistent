"""Persistent configuration for the KiCad KI-Assistent.

KiCad launches plugin actions as separate processes, so environment variables
set in the user's shell are not inherited. Settings therefore live in a small
JSON file inside the KiCad user configuration directory.

Lookup order for every setting:

1. explicit argument
2. environment variable (useful for CI)
3. this config file
4. built-in default
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import asdict, dataclass, field
from pathlib import Path

__all__ = ["AIConfig", "config_path", "load", "save"]


def config_path() -> Path:
    """Return the path of the configuration file, creating the folder."""
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home())) / "kicad" / "10.0"
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Preferences" / "kicad" / "10.0"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "kicad" / "10.0"
    return base / "ai_review.json"


@dataclass
class AIConfig:
    """User settings for the analysis backend."""

    backend: str = "ollama"          # "ollama" | "openai" | "none"
    model: str = "qwen2.5-coder:14b"
    base_url: str = "http://localhost:11434"
    api_key: str = ""
    temperature: float = 0.1
    max_tokens: int = 4096
    timeout: int = 600

    #: Rules the user switched off, by rule name.
    disabled_rules: list[str] = field(default_factory=list)

    @classmethod
    def load(cls) -> "AIConfig":
        path = config_path()
        if not path.is_file():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> Path:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def to_llm_config(self):
        """Convert to the LLM layer's config object."""
        from .llm import LLMConfig

        return LLMConfig(
            backend=self.backend,
            model=self.model,
            base_url=self.base_url,
            api_key=self.api_key,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
        )


def load() -> AIConfig:
    return AIConfig.load()


def save(config: AIConfig) -> Path:
    return config.save()