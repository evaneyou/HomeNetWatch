from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SECRET_KEY = "dev-secret-change-me-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://hns:hns-dev-password@127.0.0.1:55432/hns"
    secret_key: str = DEFAULT_SECRET_KEY
    cors_origins: str = "http://127.0.0.1:3847,http://localhost:3847"
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    session_cookie_name: str = "hns_session"
    session_max_age: int = 60 * 60 * 24 * 7
    guest_idle_seconds: int = 180
    # DB-IP City Lite file used to place guest visitors (see scripts/update-geoip.sh). Empty or missing: no places.
    geoip_db: str = "/geoip/dbip-city-lite.mmdb"

    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "admin-change-me"
    bootstrap_viewer_username: str = "viewer"
    bootstrap_viewer_password: str = "viewer-change-me"
    bootstrap_guest_username: str = "guest"
    bootstrap_guest_password: str = "guest"
    wireguard_endpoint: str = "192.0.2.10:51820"
    wireguard_state_dir: str = ""
    # Remote agents pin this key, so check-in and poll only answer over HTTPS.
    agent_require_https: bool = True
    tls_dir: str = "/var/lib/hns-tls"
    rtnp_state_dir: str = ""
    rtnp_udp_port: int = 8998
    rtnp_control_port: int = 8999
    gemini_api_key: str = ""

    @model_validator(mode="after")
    def _no_default_secret_in_production(self) -> "Settings":
        # COOKIE_SECURE=true is the production setting (see docs/deploy.md).
        if self.cookie_secure and self.secret_key == DEFAULT_SECRET_KEY:
            raise ValueError("SECRET_KEY is still the development default. Set a long random value in .env.")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
