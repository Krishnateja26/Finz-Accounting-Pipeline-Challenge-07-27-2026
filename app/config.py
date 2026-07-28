"""
Central application configuration.

All values are loaded from environment variables (see .env.example). Nothing
here should ever contain a real secret -- QuickBooks client secrets and
refresh tokens must live only in the environment / secret store, never in
source control.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_name: str = "Finz Accounting Pipeline"
    environment: str = "development"
    secret_key: str = "change-me"
    admin_reset_token: str | None = None

    # --- MongoDB ---
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db_name: str = "finz_accounting"

    # --- Gemini ---
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.6-flash"
    gemini_timeout_seconds: int = 15

    # --- QuickBooks Online ---
    qbo_client_id: str | None = None
    qbo_client_secret: str | None = None
    qbo_redirect_uri: str = "http://localhost:8000/api/quickbooks/callback"
    qbo_environment: str = "sandbox"  # sandbox | production
    qbo_realm_id: str | None = None
    qbo_access_token: str | None = None
    qbo_refresh_token: str | None = None

    # --- Accounting rules ---
    company_name: str = "BrightFix Home Services LLC"
    base_currency: str = "USD"
    fiscal_year_start_month: int = 1  # January
    accounting_basis: str = "cash"

    # --- Misc ---
    max_upload_rows_preview: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
