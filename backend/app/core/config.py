from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    MODEL_PATH: str = "weights/ppe_v4.pt"
    DATABASE_URL: str | None = None
    MINIO_ENDPOINT: str | None = None
    MINIO_ACCESS_KEY: str | None = None
    MINIO_SECRET_KEY: str | None = None
    MINIO_BUCKET_NAME: str | None = None
    MINIO_SECURE: bool = False
    # "auto" uses the first CUDA GPU when PyTorch can access one, otherwise CPU.
    # You can also force "cpu", "cuda", "cuda:0", "0", etc.
    INFERENCE_DEVICE: str = "auto"
    CONFIDENCE_THRESHOLD: float = 0.3
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
    VIDEO_VIOLATION_CONFIRM_SECONDS: float = 0.2
    VIDEO_RECENT_PPE_MEMORY_SECONDS: float = 1.5
    VIDEO_MIN_PERSON_HEIGHT_RATIO: float = 0.10
    VIDEO_STABILITY_WINDOW_FRAMES: int = 5
    VIDEO_MAX_CENTER_SHIFT_RATIO: float = 0.35
    VIDEO_MAX_SIZE_CHANGE_RATIO: float = 0.45
    VIDEO_MIN_CLEAR_PERSON_ASPECT_RATIO: float = 1.20
    VIDEO_POSTURE_HEIGHT_DROP_RATIO: float = 0.70
    VIDEO_POSTURE_HISTORY_MIN_FRAMES: int = 3
    SNAPSHOT_DIR: str = "storage/snapshots"
    UPLOAD_DIR: str = "storage/uploads"
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    # Sign-detection / auto-zone settings
    SIGN_MODEL_PATH: str = "weights/sign_model.pt"
    SIGN_CONFIDENCE_THRESHOLD: float = 0.35
    SIGN_CLASS_ZONE_MAP: dict[int, str] = {2: "RESTRICTED", 3: "RESTRICTED"}
    SIGN_CLASS_NAMES: dict[int, str] = {2: "P004_NoThoroughfare", 3: "W011_Slippery"}
    AUTO_ZONE_BUFFER_RATIO: float = 0.25
    SIGN_PASS_FRAME_INTERVAL: int = 15
    AUTO_ZONE_CONFIRM_FRAMES: int = 3
    AUTO_ZONE_DEDUPE_GRID: float = 0.05

    @model_validator(mode="after")
    def _coerce_sign_dict_keys(self) -> "Settings":
        self.SIGN_CLASS_ZONE_MAP = {int(k): v for k, v in self.SIGN_CLASS_ZONE_MAP.items()}
        self.SIGN_CLASS_NAMES = {int(k): v for k, v in self.SIGN_CLASS_NAMES.items()}
        return self

    class Config:
        env_file = str(BACKEND_DIR / ".env")


settings = Settings()
