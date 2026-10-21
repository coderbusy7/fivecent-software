from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
    # Quiet window before coalescing rapid WhatsApp bursts into one orchestrator turn.
    # Set 0 to disable (tests / debugging).
    debounce_seconds: float = 2.0


def get_settings() -> Settings:
    return Settings()
