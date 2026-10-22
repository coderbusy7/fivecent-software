from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # development | test | production — gates public /chat and Telegram auth strictness.
    app_env: str = "development"
    # Production only: when true, POST /chat requires Bearer ADMIN_API_TOKEN and is rate-limited.
    # Ignored outside production (local/dev /chat stays available for e2e).
    enable_test_chat: bool = False
    database_url: str = "sqlite+aiosqlite:///./fivecent.db"
    redis_url: str = ""
    # "arq" only if REDIS_URL is set and a worker process is running.
    queue_backend: str = "inline"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = "seed-phone-number-id"
    whatsapp_waba_id: str = ""
    whatsapp_callback_url: str = ""
    whatsapp_graph_version: str = "v21.0"
    seed_tenant_id: str = "port-vauban"
    admin_api_token: str = ""
    telegram_bot_token: str = ""
    telegram_bot_username: str = "fivecent_test_bot"
    telegram_webhook_secret: str = ""
    # Quiet window before coalescing rapid WhatsApp bursts into one orchestrator turn.
    # Set 0 to disable (tests / debugging).
    debounce_seconds: float = 2.0

    @field_validator("app_env")
    @classmethod
    def _app_env(cls, value: str) -> str:
        normalized = (value or "development").strip().lower()
        if normalized not in {"development", "test", "production"}:
            raise ValueError("APP_ENV must be development, test, or production.")
        return normalized

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def chat_enabled(self) -> bool:
        if not self.is_production:
            return True
        return bool(self.enable_test_chat)


def get_settings() -> Settings:
    return Settings()
