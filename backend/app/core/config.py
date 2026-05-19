from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    MODEL_PATH: str = "weights/best.pt"
    CONFIDENCE_THRESHOLD: float = 0.5
    # Minimum fraction of an equipment box that must overlap its person box
    # for the two to be considered associated (0.0 – 1.0)
    PPE_OVERLAP_THRESHOLD: float = 0.3
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000"]

    class Config:
        env_file = ".env"


settings = Settings()
