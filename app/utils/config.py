import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    ALLOWED_ORIGINS: list = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://localhost:3000",
        os.getenv("FRONTEND_URL", ""),
        os.getenv("VERCEL_URL", ""),
    ]
    PORT: int = int(os.getenv("PORT", 8000))
    MAX_PIPELINE_ATTEMPTS: int = 3
    MAX_REPAIR_ATTEMPTS: int = 3
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    GROQ_TPM_LIMIT: int = int(os.getenv("GROQ_TPM_LIMIT", "12000"))
    GROQ_OTPM_LIMIT: int | None = int(os.getenv("GROQ_OTPM_LIMIT")) if os.getenv("GROQ_OTPM_LIMIT") else None
    REASONING_EFFORT: str | None = (
        os.getenv("REASONING_EFFORT", "").strip()
        or ("low" if os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").startswith("openai/gpt-oss") else None)
    )
    PIPELINE_VERSION: str = "1.0.0"


config = Config()
