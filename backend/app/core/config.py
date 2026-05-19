from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    MODEL_PATH: str = "weights/best.pt"
    CONFIDENCE_THRESHOLD: float = 0.5
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000"]

    class Config:
        env_file = ".env"


settings = Settings()
