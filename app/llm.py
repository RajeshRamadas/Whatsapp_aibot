"""Chat and embedding providers behind one small interface.

Chat: OpenAI, Anthropic (Claude) or Google Gemini, chosen per tenant.
Embeddings: OpenAI or Gemini, chosen once for the whole deployment.
SDK clients are created lazily, so only the providers you configure need a key.
"""
from functools import cache
from typing import Protocol

from .config import DEFAULT_CHAT_MODELS, DEFAULT_EMBEDDING_MODELS, settings

CHAT_PROVIDERS = tuple(DEFAULT_CHAT_MODELS)
EMBEDDING_PROVIDERS = tuple(DEFAULT_EMBEDDING_MODELS)

REFUSAL_REPLY = "Sorry, I can't help with that request."


class ProviderNotConfigured(RuntimeError):
    pass


class ChatProvider(Protocol):
    def complete(self, model: str, system: str, messages: list[dict]) -> str:
        """messages alternate {'role': 'user' | 'assistant', 'content': str} and end with a user turn."""


class Embedder(Protocol):
    def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]: ...


def _require(key: str | None, name: str) -> str:
    if not key:
        raise ProviderNotConfigured(f'{name} is not set')
    return key


class OpenAIChat:
    def __init__(self):
        from openai import OpenAI
        self.client = OpenAI(api_key=_require(settings.openai_api_key, 'OPENAI_API_KEY'),
                             timeout=settings.llm_timeout_seconds, max_retries=2)

    def complete(self, model, system, messages):
        response = self.client.chat.completions.create(
            model=model, messages=[{'role': 'system', 'content': system}, *messages])
        return response.choices[0].message.content or ''


class AnthropicChat:
    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic(api_key=_require(settings.anthropic_api_key, 'ANTHROPIC_API_KEY'),
                                          timeout=settings.llm_timeout_seconds, max_retries=2)

    def complete(self, model, system, messages):
        response = self.client.beta.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            messages=messages,
            output_config={'effort': 'low'},  # short grounded answers don't need deep reasoning
            # On a safety refusal, let the API retry on a suitable fallback model.
            betas=['server-side-fallback-2026-07-01'],
            fallbacks='default',
        )
        if response.stop_reason == 'refusal':
            return REFUSAL_REPLY
        return ''.join(block.text for block in response.content if block.type == 'text')


class GeminiChat:
    def __init__(self):
        from google import genai
        self.client = genai.Client(api_key=_require(settings.gemini_api_key, 'GEMINI_API_KEY'))

    def complete(self, model, system, messages):
        from google.genai import types
        contents = [types.Content(role='model' if m['role'] == 'assistant' else 'user',
                                  parts=[types.Part(text=m['content'])]) for m in messages]
        response = self.client.models.generate_content(
            model=model, contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                http_options=types.HttpOptions(timeout=int(settings.llm_timeout_seconds * 1000))))
        return response.text or ''


class OpenAIEmbedder:
    def __init__(self, model: str):
        from openai import OpenAI
        self.model = model
        self.client = OpenAI(api_key=_require(settings.openai_api_key, 'OPENAI_API_KEY'),
                             timeout=settings.llm_timeout_seconds, max_retries=2)

    def embed(self, texts, *, query=False):
        response = self.client.embeddings.create(model=self.model, input=texts,
                                                 dimensions=settings.embedding_dimensions)
        return [item.embedding for item in response.data]


class GeminiEmbedder:
    def __init__(self, model: str):
        from google import genai
        self.model = model
        self.client = genai.Client(api_key=_require(settings.gemini_api_key, 'GEMINI_API_KEY'))

    def embed(self, texts, *, query=False):
        from google.genai import types
        response = self.client.models.embed_content(
            model=self.model, contents=texts,
            config=types.EmbedContentConfig(
                task_type='RETRIEVAL_QUERY' if query else 'RETRIEVAL_DOCUMENT',
                output_dimensionality=settings.embedding_dimensions))
        return [e.values for e in response.embeddings]


_CHAT_CLASSES = {'openai': OpenAIChat, 'anthropic': AnthropicChat, 'gemini': GeminiChat}
_EMBEDDER_CLASSES = {'openai': OpenAIEmbedder, 'gemini': GeminiEmbedder}


@cache
def chat_provider(name: str) -> ChatProvider:
    if name not in _CHAT_CLASSES:
        raise ProviderNotConfigured(f'Unknown chat provider {name!r}')
    return _CHAT_CLASSES[name]()


@cache
def embedder() -> Embedder:
    name = settings.embedding_provider
    if name not in _EMBEDDER_CLASSES:
        raise ProviderNotConfigured(f'Unknown embedding provider {name!r}')
    return _EMBEDDER_CLASSES[name](settings.embedding_model or DEFAULT_EMBEDDING_MODELS[name])


def resolve_chat(provider: str | None, model: str | None) -> tuple[str, str]:
    """Tenant setting first, then the server default."""
    provider = provider or settings.chat_provider
    if model is None and provider == settings.chat_provider:
        model = settings.chat_model
    return provider, model or DEFAULT_CHAT_MODELS[provider]
