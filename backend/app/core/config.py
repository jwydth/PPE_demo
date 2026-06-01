from pathlib import Path

from pydantic_settings import BaseSettings

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    MODEL_PATH: str = "weights/ppe_v1.pt"
    CONFIDENCE_THRESHOLD: float = 0.5
    # Minimum fraction of an equipment box that must overlap its person box
    # for the two to be considered associated (0.0 – 1.0)
    PPE_OVERLAP_THRESHOLD: float = 0.3
    VIDEO_FRAME_STRIDE: int = 1
    VIDEO_TRACKER: str = "bytetrack.yaml"
    VIDEO_CASE_IOU_THRESHOLD: float = 0.2
    VIDEO_CASE_CENTER_DISTANCE_RATIO: float = 0.75
    VIDEO_CASE_MAX_FRAME_GAP: int = 90
    VIDEO_EDGE_MARGIN_RATIO: float = 0.05
    VIDEO_NEW_TRACK_GRACE_SECONDS: float = 0.5
    VIDEO_VIOLATION_CONFIRM_SECONDS: float = 0.75
    VIDEO_RECENT_PPE_MEMORY_SECONDS: float = 1.5
    VIDEO_MIN_PERSON_HEIGHT_RATIO: float = 0.2
    VIDEO_STABILITY_WINDOW_FRAMES: int = 5
    VIDEO_MAX_CENTER_SHIFT_RATIO: float = 0.35
    VIDEO_MAX_SIZE_CHANGE_RATIO: float = 0.45
    VIOLATION_DB_PATH: str = "storage/violations.sqlite3"
    SNAPSHOT_DIR: str = "storage/snapshots"
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    class Config:
        env_file = str(BACKEND_DIR / ".env")


settings = Settings()
