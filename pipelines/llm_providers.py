"""LLM provider abstraction for case law analysis.

Add a new provider by subclassing LLMProvider, implementing `complete`,
and registering it in the PROVIDERS dict.

Environment variables:
    ANTHROPIC_API_KEY   — required for AnthropicProvider
    OPENAI_API_KEY      — required for OpenAIProvider
    GOOGLE_API_KEY      — required for GoogleProvider
"""

from __future__ import annotations

import functools
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


@functools.lru_cache(maxsize=1)
def load_prompt() -> str:
    """Read PROMPT.md once and cache it for the lifetime of the process."""
    prompt_path = Path(__file__).parent / "PROMPT.md"
    return prompt_path.read_text(encoding="utf-8")


class LLMProvider(ABC):
    """Abstract base for LLM providers.

    Subclass this and implement `complete(case_text) -> str`.
    The method receives extracted case law text and must return the raw LLM
    response string, expected to be JSON matching the PROMPT.md schema.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable identifier, e.g. 'anthropic/claude-opus-4-6'."""
        ...

    @abstractmethod
    def complete(self, case_text: str) -> str:
        """Send the case law text to the LLM and return the raw response.

        Args:
            case_text: Full text extracted from the legal judgment PDF.

        Returns:
            Raw LLM response string, expected to be valid JSON.

        Raises:
            Any vendor SDK exception on API or network failure.
        """
        ...

    def _build_message(self, case_text: str) -> str:
        """Combine the PROMPT.md template with the extracted case text."""
        return f"{load_prompt()}\n\n# CASE LAW TEXT\n\n{case_text}"


class AnthropicProvider(LLMProvider):
    """Claude via the Anthropic API.

    Install: pip install anthropic
    Env var: ANTHROPIC_API_KEY
    """

    DEFAULT_MODEL = "claude-opus-4-6"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        max_tokens: int = 4096,
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "anthropic package is required: pip install 'pipelines[llm]'"
            ) from exc

        self._model = model or self.DEFAULT_MODEL
        self._max_tokens = max_tokens
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ["ANTHROPIC_API_KEY"]
        )

    @property
    def name(self) -> str:
        return f"anthropic/{self._model}"

    def complete(self, case_text: str) -> str:
        message = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": self._build_message(case_text)}],
        )
        return message.content[0].text


class OpenAIProvider(LLMProvider):
    """GPT via the OpenAI API.

    Install: pip install openai
    Env var: OPENAI_API_KEY
    """

    DEFAULT_MODEL = "gpt-4o"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        max_tokens: int = 4096,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "openai package is required: pip install 'pipelines[llm]'"
            ) from exc

        self._model = model or self.DEFAULT_MODEL
        self._max_tokens = max_tokens
        self._client = OpenAI(api_key=api_key or os.environ["OPENAI_API_KEY"])

    @property
    def name(self) -> str:
        return f"openai/{self._model}"

    def complete(self, case_text: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": self._build_message(case_text)}],
        )
        return response.choices[0].message.content


class GoogleProvider(LLMProvider):
    """Gemini via the Google Generative AI API.

    Install: pip install google-generativeai
    Env var: GOOGLE_API_KEY
    """

    DEFAULT_MODEL = "gemini-3-flash-preview"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        max_tokens: int = 4096,
    ) -> None:
        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise ImportError(
                "google-generativeai package is required: pip install 'pipelines[llm]'"
            ) from exc

        self._model_name = model or self.DEFAULT_MODEL
        self._max_tokens = max_tokens
        genai.configure(api_key=api_key or os.environ["GOOGLE_API_KEY"])
        self._model_client = genai.GenerativeModel(self._model_name)

    @property
    def name(self) -> str:
        return f"google/{self._model_name}"

    def complete(self, case_text: str) -> str:
        from google.generativeai.types import GenerationConfig

        response = self._model_client.generate_content(
            self._build_message(case_text),
            generation_config=GenerationConfig(max_output_tokens=self._max_tokens),
        )
        return response.text


# ── Registry & factory ─────────────────────────────────────────────────────────

PROVIDERS: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "google": GoogleProvider,
}


def get_provider(name: str, **kwargs: Any) -> LLMProvider:
    """Instantiate a provider by name.

    Args:
        name:     Provider key — "anthropic", "openai", or "google".
        **kwargs: Forwarded to the provider constructor (model, api_key, max_tokens).

    Example:
        provider = get_provider("anthropic", model="claude-opus-4-6")
        provider = get_provider("openai", model="gpt-4o", max_tokens=8192)
    """
    if name not in PROVIDERS:
        raise ValueError(
            f"Unknown provider '{name}'. Available: {sorted(PROVIDERS)}"
        )
    return PROVIDERS[name](**kwargs)
