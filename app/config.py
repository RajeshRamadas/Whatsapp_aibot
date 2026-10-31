import re

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Used when neither the tenant nor CHAT_MODEL / EMBEDDING_MODEL names a model.
DEFAULT_CHAT_MODELS = {
    'openai': 'gpt-4.1-mini',
    'anthropic': 'claude-opus-5-5',
    'gemini': 'gemini-2.5-flash',
}
DEFAULT_EMBEDDING_MODELS = {
    'openai': 'text-embedding-3-small',
    'gemini': 'gemini-embedding-001',
}


class Settings(BaseSettings):
    database_url: str
    admin_bootstrap_token: str
    auth_cookie_secure: bool = True

    # LLM providers. Only the providers you use need a key.
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    chat_provider: str = 'openai'  # openai | anthropic | gemini; tenants can override
    chat_model: str | None = None  # defaults to DEFAULT_CHAT_MODELS[chat_provider]
    llm_timeout_seconds: float = 60
    # Embeddings are shared by every tenant: changing provider, model or
    # dimensions requires recreating the chunks table and re-ingesting.
    embedding_provider: str = 'openai'  # openai | gemini
    embedding_model: str | None = None
    embedding_dimensions: int = 1536

    # Retrieval
    retrieval_top_k: int = 6
    retrieval_candidates: int = 20  # per search method, before the results are merged
    retrieval_max_distance: float = 0.8  # cosine distance; chunks further away are ignored
    # Postgres text search language for keyword matching ('english' stems and drops stopwords;
    # 'simple' matches words exactly, better for mixed-language content). Changing it needs a fresh chunks table.
    text_search_config: str = 'english'
    query_rewrite: bool = True  # turn follow-ups like "and for shipping?" into a full question before searching
    history_messages: int = 8

    storage_backend: str = 'local'  # local | s3
    storage_local_path: str = 'uploads'
    storage_bucket: str = 'chatbot-uploads'
    storage_endpoint_url: str | None = None
    storage_region: str = 'us-east-1'
    max_upload_mb: int = 15
    chat_rate_limit_per_minute: int = 20

    # WhatsApp Cloud API (Meta). The webhook is disabled until all three are set.
    whatsapp_verify_token: str | None = None
    whatsapp_app_secret: str | None = None
    whatsapp_access_token: str | None = None
    whatsapp_graph_version: str = 'v23.0'
    whatsapp_handoff_keywords: str = 'human,agent'

    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    @field_validator('text_search_config')
    @classmethod
    def _plain_identifier(cls, value: str) -> str:
        if not re.fullmatch(r'[a-z_]+', value):  # it is placed in SQL as a regconfig name
            raise ValueError('TEXT_SEARCH_CONFIG must be a Postgres text search configuration name, like english')
        return value

    @property
    def whatsapp_enabled(self) -> bool:
        return bool(self.whatsapp_verify_token and self.whatsapp_app_secret and self.whatsapp_access_token)

    @property
    def handoff_keywords(self) -> set[str]:
        return {k.strip().lower() for k in self.whatsapp_handoff_keywords.split(',') if k.strip()}


settings = Settings()
