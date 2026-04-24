"""
Configuração da aplicação — lê variáveis de ambiente via pydantic-settings
e valida na inicialização. Falha cedo e com mensagem clara se algo crítico
faltar ou estiver inválido.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Core ---
    ENV: str = "production"
    LOG_LEVEL: str = "INFO"
    PORT: int = 8000

    # --- Database ---
    # Se não definido, cai em SQLite local — útil apenas para smoke tests.
    DATABASE_URL: str = "sqlite:///./dev.db"

    # --- Auth (obrigatórios) ---
    ACCESS_PASSWORD: str
    SESSION_SECRET: str

    # --- LLM ---
    OPENROUTER_API_KEY: str
    OPENROUTER_MODEL_POTENTE: str = "x-ai/grok-4"
    OPENROUTER_MODEL_BARATO: str = "x-ai/grok-4-fast"

    # Modelos que o usuário pode escolher na tela de upload para o cadastro
    # assistido. Whitelist para evitar uso de modelos arbitrários (risco de
    # custo). `suporta_visao` controla se o PDF vai como imagem (PDFs
    # escaneados) ou só como texto. Se precisar adicionar, edite esta lista.
    @property
    def modelos_potentes_catalogo(self) -> list[dict[str, object]]:
        return [
            {"id": "anthropic/claude-opus-4.7", "suporta_visao": True},
            {"id": "anthropic/claude-sonnet-4.6", "suporta_visao": True},
            {"id": "openai/gpt-5.4", "suporta_visao": True},
            {"id": "openai/gpt-5.4-mini", "suporta_visao": True},
            {"id": "google/gemini-3-flash-preview", "suporta_visao": True},
            {"id": "deepseek/deepseek-v4-pro", "suporta_visao": False},
            {"id": "x-ai/grok-4.1-fast", "suporta_visao": False},
            {"id": "x-ai/grok-4-fast", "suporta_visao": False},
        ]

    @property
    def modelos_potentes_permitidos(self) -> list[str]:
        return [m["id"] for m in self.modelos_potentes_catalogo]

    def modelo_suporta_visao(self, modelo: str) -> bool:
        for m in self.modelos_potentes_catalogo:
            if m["id"] == modelo:
                return bool(m["suporta_visao"])
        for m in self.modelos_baratos_catalogo:
            if m["id"] == modelo:
                return bool(m["suporta_visao"])
        # Modelo fora do catálogo (usando default do env): assume que suporta
        # visão — os defaults padrão (Grok-4, GPT-4o) historicamente suportam.
        return True

    # Modelos baratos oferecidos no cadastro para o fallback IA em futuras
    # extrações (quando plumber/OCR não conseguem extrair todas as linhas).
    # Escolhidos por oferecerem bom custo-benefício.
    @property
    def modelos_baratos_catalogo(self) -> list[dict[str, object]]:
        return [
            {"id": "x-ai/grok-4.1-fast", "suporta_visao": False},
            {"id": "x-ai/grok-4-fast", "suporta_visao": False},
            {"id": "deepseek/deepseek-v4-pro", "suporta_visao": False},
            {"id": "google/gemini-3-flash-preview", "suporta_visao": True},
        ]

    @property
    def modelos_baratos_permitidos(self) -> list[str]:
        return [m["id"] for m in self.modelos_baratos_catalogo]

    # --- Uploads ---
    MAX_UPLOAD_SIZE_MB: int = 20

    # --- Integrations ---
    DEFAULT_WEBHOOK_URL: str | None = None
    ALLOWED_ORIGINS: str = ""

    # --- Conformidade ---
    SCORE_CONFORMIDADE_MIN: float = 0.85
    SCORE_CONFORMIDADE_ALERTA: float = 0.70
    TAXA_SUCESSO_MIN_ESQUELETO: float = 0.70

    @field_validator("ACCESS_PASSWORD")
    @classmethod
    def _check_password_len(cls, v: str) -> str:
        if len(v) < 16:
            raise ValueError(
                "ACCESS_PASSWORD precisa ter pelo menos 16 caracteres."
            )
        return v

    @field_validator("SESSION_SECRET")
    @classmethod
    def _check_secret_len(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError(
                "SESSION_SECRET precisa ter pelo menos 32 caracteres."
            )
        return v

    @field_validator("DATABASE_URL")
    @classmethod
    def _fix_postgres_url(cls, v: str) -> str:
        # Railway (e Heroku) injetam DATABASE_URL como 'postgres://' mas
        # SQLAlchemy 2.0 espera 'postgresql://'. Converte silenciosamente.
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql://", 1)
        return v

    @field_validator("SCORE_CONFORMIDADE_MIN", "SCORE_CONFORMIDADE_ALERTA", "TAXA_SUCESSO_MIN_ESQUELETO")
    @classmethod
    def _check_score_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("Scores precisam estar entre 0.0 e 1.0.")
        return v

    @property
    def is_dev(self) -> bool:
        return self.ENV.lower() in {"development", "dev", "local"}

    @property
    def is_prod(self) -> bool:
        return not self.is_dev

    @property
    def allowed_origins_list(self) -> list[str]:
        if not self.ALLOWED_ORIGINS:
            return []
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
