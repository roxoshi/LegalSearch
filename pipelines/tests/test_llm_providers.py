"""Tests for pipelines.llm_providers."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from pipelines.llm_providers import (
    PROVIDERS,
    AnthropicProvider,
    GoogleProvider,
    LLMProvider,
    OpenAIProvider,
    get_provider,
    load_prompt,
)


# ── load_prompt ────────────────────────────────────────────────────────────────


def test_load_prompt_returns_nonempty_string():
    prompt = load_prompt()
    assert isinstance(prompt, str)
    assert len(prompt) > 100


def test_load_prompt_contains_schema_fields():
    prompt = load_prompt()
    for field in (
        "summary",
        "facts",
        "issues",
        "ratio_decidendi",
        "conclusion",
    ):
        assert field in prompt, f"Expected '{field}' in PROMPT.md"


def test_load_prompt_is_cached():
    """lru_cache must return the same object on repeated calls."""
    assert load_prompt() is load_prompt()


# ── PROVIDERS registry ─────────────────────────────────────────────────────────


def test_providers_registry_has_expected_keys():
    assert set(PROVIDERS) >= {"anthropic", "openai", "google"}


def test_all_registered_providers_are_subclasses():
    for name, cls in PROVIDERS.items():
        assert issubclass(cls, LLMProvider), f"{name} must subclass LLMProvider"


# ── get_provider factory ───────────────────────────────────────────────────────


def test_get_provider_raises_for_unknown_name():
    with pytest.raises(ValueError, match="Unknown provider"):
        get_provider("grok")


def test_get_provider_error_message_lists_valid_names():
    with pytest.raises(ValueError, match="anthropic"):
        get_provider("invalid")


# ── AnthropicProvider ──────────────────────────────────────────────────────────


class TestAnthropicProvider:
    @pytest.fixture()
    def mock_sdk(self, monkeypatch):
        """Inject a mock anthropic module so no real SDK is needed."""
        mock_module = MagicMock()
        mock_client = MagicMock()
        mock_module.Anthropic.return_value = mock_client
        monkeypatch.setitem(sys.modules, "anthropic", mock_module)
        return mock_module, mock_client

    def test_name_uses_given_model(self, mock_sdk, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        provider = AnthropicProvider(model="claude-test-model")
        assert provider.name == "anthropic/claude-test-model"

    def test_name_uses_default_model_when_none_given(self, mock_sdk, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        provider = AnthropicProvider()
        assert provider.name == f"anthropic/{AnthropicProvider.DEFAULT_MODEL}"

    def test_complete_calls_api_and_returns_text(self, mock_sdk, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        _, mock_client = mock_sdk
        expected = '{"summary": "test response"}'
        mock_client.messages.create.return_value.content = [MagicMock(text=expected)]

        provider = AnthropicProvider(api_key="k")
        result = provider.complete("GST case text here")

        assert result == expected
        mock_client.messages.create.assert_called_once()
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 4096
        assert "GST case text here" in call_kwargs["messages"][0]["content"]

    def test_complete_passes_custom_max_tokens(self, mock_sdk, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        _, mock_client = mock_sdk
        mock_client.messages.create.return_value.content = [MagicMock(text="{}")]

        provider = AnthropicProvider(api_key="k", max_tokens=2048)
        provider.complete("text")

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 2048

    def test_missing_package_raises_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "anthropic", None)
        with pytest.raises(ImportError, match="anthropic"):
            AnthropicProvider(api_key="k")

    def test_missing_api_key_env_raises(self, mock_sdk, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(KeyError):
            AnthropicProvider()  # no api_key kwarg, no env var

    def test_explicit_api_key_bypasses_env(self, mock_sdk, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        mock_module, _ = mock_sdk
        # Should not raise even without the env var
        AnthropicProvider(api_key="explicit-key")
        mock_module.Anthropic.assert_called_once_with(api_key="explicit-key")


# ── OpenAIProvider ─────────────────────────────────────────────────────────────


class TestOpenAIProvider:
    @pytest.fixture()
    def mock_sdk(self, monkeypatch):
        mock_module = MagicMock()
        mock_client = MagicMock()
        mock_module.OpenAI.return_value = mock_client
        monkeypatch.setitem(sys.modules, "openai", mock_module)
        return mock_module, mock_client

    def test_name_uses_given_model(self, mock_sdk, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        provider = OpenAIProvider(model="gpt-test")
        assert provider.name == "openai/gpt-test"

    def test_name_uses_default_model(self, mock_sdk, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        provider = OpenAIProvider()
        assert provider.name == f"openai/{OpenAIProvider.DEFAULT_MODEL}"

    def test_complete_calls_api_and_returns_content(self, mock_sdk, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        _, mock_client = mock_sdk
        expected = '{"summary": "openai result"}'
        mock_client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content=expected))
        ]

        provider = OpenAIProvider(api_key="k")
        result = provider.complete("some case text")

        assert result == expected
        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "some case text" in call_kwargs["messages"][0]["content"]

    def test_missing_package_raises_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "openai", None)
        with pytest.raises(ImportError, match="openai"):
            OpenAIProvider(api_key="k")

    def test_missing_api_key_env_raises(self, mock_sdk, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(KeyError):
            OpenAIProvider()


# ── GoogleProvider ─────────────────────────────────────────────────────────────


class TestGoogleProvider:
    @pytest.fixture()
    def mock_sdk(self, monkeypatch):
        mock_genai = MagicMock()
        mock_model_instance = MagicMock()
        mock_genai.GenerativeModel.return_value = mock_model_instance
        mock_types = MagicMock()

        monkeypatch.setitem(sys.modules, "google", MagicMock(generativeai=mock_genai))
        monkeypatch.setitem(sys.modules, "google.generativeai", mock_genai)
        monkeypatch.setitem(sys.modules, "google.generativeai.types", mock_types)
        return mock_genai, mock_model_instance, mock_types

    def test_name_uses_given_model(self, mock_sdk, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "k")
        provider = GoogleProvider(model="gemini-test")
        assert provider.name == "google/gemini-test"

    def test_name_uses_default_model(self, mock_sdk, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "k")
        provider = GoogleProvider()
        assert provider.name == f"google/{GoogleProvider.DEFAULT_MODEL}"

    def test_complete_calls_api_and_returns_text(self, mock_sdk, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "k")
        mock_genai, mock_model_instance, _ = mock_sdk
        expected = '{"summary": "gemini result"}'
        mock_model_instance.generate_content.return_value.text = expected

        provider = GoogleProvider(api_key="k")
        result = provider.complete("case text")

        assert result == expected
        mock_model_instance.generate_content.assert_called_once()

    def test_missing_package_raises_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "google.generativeai", None)
        with pytest.raises(ImportError, match="google-generativeai"):
            GoogleProvider(api_key="k")


# ── _build_message (base class) ────────────────────────────────────────────────


class TestBuildMessage:
    @pytest.fixture()
    def mock_anthropic_sdk(self, monkeypatch):
        mock_module = MagicMock()
        monkeypatch.setitem(sys.modules, "anthropic", mock_module)
        return mock_module

    def test_message_contains_prompt_and_case_text(
        self, mock_anthropic_sdk, monkeypatch
    ):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        provider = AnthropicProvider(api_key="k")
        msg = provider._build_message("my important case text")

        assert load_prompt() in msg
        assert "my important case text" in msg

    def test_message_has_section_separator(self, mock_anthropic_sdk, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        provider = AnthropicProvider(api_key="k")
        msg = provider._build_message("text")

        assert "CASE LAW TEXT" in msg
