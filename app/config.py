import re
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .invoice_config import InvoiceIssuer

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
    redis_url: str = 'redis://localhost:6379/0'
    admin_bootstrap_token: str
    inbox_vapid_private_key: str | None = None
    inbox_vapid_subject: str | None = None
    inbox_vapid_public_key: str | None = None
    auth_cookie_secure: bool = True
    environment: str = 'development'
    metrics_token: str | None = None
    sentry_dsn: str | None = None
    email_backend: Literal['console', 'ses'] = 'console'
    email_from: str | None = None
    email_region: str = 'ap-south-1'
    email_sns_topic_arn: str | None = None
    email_configuration_set: str | None = None
    email_sends_per_second: int = Field(1, ge=1, le=1000)
    public_app_url: str = 'http://localhost:3000'
    contact_import_max_bytes: int = Field(1048576, ge=1024, le=10485760)
    contact_import_max_rows: int = Field(1000, ge=1, le=10000)
    master_key: str | None = None
    master_key_version: int = 1
    master_key_previous: dict[str, str] = {}

    # LLM providers. Only the providers you use need a key.
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    chat_provider: str = 'openai'  # openai | anthropic | gemini; tenants can override
    chat_model: str | None = None  # defaults to DEFAULT_CHAT_MODELS[chat_provider]
    gst_invoice_issuer: InvoiceIssuer | None = None
    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = None
    razorpay_webhook_secret: str | None = None
    razorpay_plan_ids: dict[str, str] = Field(default_factory=dict)
    ai_prices_usd: dict[str, tuple[Decimal, Decimal]] = Field(default_factory=dict)
    llm_timeout_seconds: float = 60
    # Embeddings are shared by every tenant: changing provider, model or
    # dimensions requires recreating the chunks table and re-ingesting.
    embedding_provider: str = 'openai'  # openai | gemini
    embedding_model: str | None = None
    embedding_dimensions: int = 1536

    # Retrieval
    qa_min_similarity: float = Field(0.9, ge=0, le=1)
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

    # WhatsApp Cloud API (Meta). Webhook verification requires the verify token and app secret.
    whatsapp_verify_token: str | None = None
    whatsapp_app_id: str | None = None
    whatsapp_signup_config_id: str | None = None
    whatsapp_app_secret: str | None = None
    whatsapp_access_token: str | None = None
    whatsapp_graph_version: str = 'v23.0'
    whatsapp_handoff_keywords: str = 'human,agent'

    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    @field_validator('ai_prices_usd')
    @classmethod
    def valid_ai_prices(cls, prices):
        if any(not rate.is_finite() or rate < 0 or rate > 1000000 for pair in prices.values() for rate in pair):
            raise ValueError('AI prices must be finite nonnegative USD amounts per million tokens')
        return prices

    @field_validator('text_search_config')
    @classmethod
    def _plain_identifier(cls, value: str) -> str:
        if not re.fullmatch(r'[a-z_]+', value):  # it is placed in SQL as a regconfig name
            raise ValueError('TEXT_SEARCH_CONFIG must be a Postgres text search configuration name, like english')
        return value

    @property
    def whatsapp_enabled(self) -> bool:
        return bool(self.whatsapp_verify_token and self.whatsapp_app_secret)

    @property
    def handoff_keywords(self) -> set[str]:
        return {k.strip().lower() for k in self.whatsapp_handoff_keywords.split(',') if k.strip()}


settings = Settings()
