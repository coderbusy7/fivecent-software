from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./fivecent.db"
    redis_url: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = "seed-phone-number-id"
    whatsapp_graph_version: str = "v21.0"
    seed_tenant_id: str = "port-vauban"
    admin_api_token: str = ""


def get_settings() -> Settings:
    return Settings()
