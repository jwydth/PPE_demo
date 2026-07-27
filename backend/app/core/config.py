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
    # Bounded pool of pre-warmed model instances for the live streaming path —
    # caps VRAM/connect-latency instead of loading a fresh copy of the weights
    # per websocket connection. Connections beyond this count queue for a free
    # instance (see PPEDetector.acquire_model_instance).
    MAX_CONCURRENT_STREAMS: int = 4
    INFERENCE_HALF: bool = False   # set True only on a CUDA GPU
    INFERENCE_IMGSZ: int = 640     # pin inference resolution for predictable latency
    CONFIDENCE_THRESHOLD: float = 0.3
    # Minimum fraction of an equipment box that must overlap its person box
    # for the two to be considered associated (0.0 – 1.0)
    PPE_OVERLAP_THRESHOLD: float = 0.3
    VIDEO_FRAME_STRIDE: int = 1
    VIDEO_TRACKER: str = "bytetrack.yaml"
    VIDEO_CASE_IOU_THRESHOLD: float = 0.2
    VIDEO_CASE_CENTER_DISTANCE_RATIO: float = 0.75
    VIDEO_CASE_MAX_FRAME_GAP: int = 90
    VIDEO_PPE_DUPLICATE_SUPPRESSION_GAP: int = 300
    VIDEO_PPE_DUPLICATE_CENTER_DISTANCE_RATIO: float = 1.25
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
    SIGN_CLASS_ZONE_MAP: dict[int, str] = {2: "RESTRICTED", 3: "SLIPPERY"}
    SIGN_CLASS_NAMES: dict[int, str] = {
        0: "M001_MustWearHardHat",
        1: "M002_MustWearSafetyVest",
        2: "P004_NoThoroughfare",
        3: "W011_Slippery",
    }
    # Sign classes that should trigger PPE detection (not zone creation)
    SIGN_CLASS_PPE_TRIGGER: set[int] = {0, 1}
    AUTO_ZONE_BUFFER_RATIO: float = 0.25
    SIGN_PASS_FRAME_INTERVAL: int = 15
    AUTO_ZONE_CONFIRM_FRAMES: int = 3
    AUTO_PPE_CONFIRM_FRAMES: int = 1
    AUTO_ZONE_DEDUPE_GRID: float = 0.05
    # A zone sign must hold still (within AUTO_ZONE_MOVE_TOLERANCE of where its
    # still-streak began) for this many seconds before a zone is suggested. This
    # prevents a sign being carried across the floor from creating a zone — only
    # a sign that has been put down and left in place triggers one.
    AUTO_ZONE_STATIONARY_SECONDS: float = 3.0
    AUTO_ZONE_MOVE_TOLERANCE: float = 0.03  # max center drift (fraction of frame) still counted as "still"
    # Gap in seconds without a detection that is treated as the worker having exited any zone
    VIDEO_ZONE_REENTRY_GAP_SECONDS: float = 1.0
    # When zone monitoring is enabled but no WALKWAY zone is defined, every
    # detected walker is treated as being outside a walkway.  A violation is
    # raised once the worker has been visible for this many consecutive seconds.
    NO_WALKWAY_DWELL_SECONDS: float = 1.5
    # Fall-detection / behavior incident settings.
    FALL_MODEL_PATH: str = "weights/yolo26m-pose.pt"
    FALL_PERSON_CONFIDENCE: float = 0.10
    FALL_RISK_THRESHOLD: float = 0.52
    FALL_THRESHOLD: float = 0.68
    FALL_PERSISTENCE_SECONDS: float = 1.0
    FALL_MAX_FRAMES: int = 1200
    FALL_FRAME_STRIDE: int = 1
    FALL_LIVE_FRAME_STRIDE: int = 5
    FALL_INCIDENT_COOLDOWN_SECONDS: float = 10.0
    FALL_MODEL_NAME: str = "yolo26m-pose"
    FALL_MODEL_VERSION: str = "v8.4.0"
    # Upper bound on rows UnifiedIncidentService will read per category per call.
    # Analytics aggregates in Python (see analytics_service.py's module docstring
    # for why), so a date range with more incidents than this gets its oldest
    # rows silently dropped from the counts. Raise this if real incident volume
    # approaches it; a truncation warning is logged when it's hit either way.
    ANALYTICS_LIMIT: int = 20000

    @model_validator(mode="after")
    def _coerce_sign_dict_keys(self) -> "Settings":
        self.SIGN_CLASS_ZONE_MAP = {int(k): v for k, v in self.SIGN_CLASS_ZONE_MAP.items()}
        self.SIGN_CLASS_NAMES = {int(k): v for k, v in self.SIGN_CLASS_NAMES.items()}
        self.SIGN_CLASS_PPE_TRIGGER = {int(k) for k in self.SIGN_CLASS_PPE_TRIGGER}
        return self

    class Config:
        env_file = str(BACKEND_DIR / ".env")


settings = Settings()
