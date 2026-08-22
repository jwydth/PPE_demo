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
    # Three streams support the Matrix View camera set. Extra streams queue
    # instead of exhausting VRAM and crashing all active streams.
    MAX_CONCURRENT_STREAMS: int = 3
    # How long a camera's shared frame-capture thread (CameraFrameHub) stays
    # connected after its last subscriber disconnects before it actually
    # tears down the RTSP/capture session. A page reload or a StrictMode
    # dev-mode remount closes and reopens the websocket within a second or
    # two; without this grace window every such reconnect pays the full
    # RTSP handshake (TCP connect + SETUP/PLAY + wait for a keyframe) again,
    # which is what makes the live feed go black for a few seconds on
    # reload. Sized to comfortably cover a slow reload, a brief tab switch,
    # or a user reopening the dashboard a moment later — at 8s a reload that
    # took slightly longer still fell off the edge and paid a full cold
    # start. Set to 0 to tear down immediately (previous behavior).
    FRAME_HUB_IDLE_GRACE_SECONDS: float = 60.0
    INFERENCE_HALF: bool = True    # applied only on CUDA by the pipeline
    INFERENCE_IMGSZ: int = 640     # pin inference resolution for predictable latency
    CONFIDENCE_THRESHOLD: float = 0.3
    # Minimum fraction of an equipment box that must overlap its person box
    # for the two to be considered associated (0.0 – 1.0)
    PPE_OVERLAP_THRESHOLD: float = 0.3
    VIDEO_FRAME_STRIDE: int = 1
    LIVE_PPE_TARGET_FPS: float = 8.0
    # Server-composed live output. AI remains asynchronous; the compositor
    # releases each buffered source frame at its presentation deadline.
    ANNOTATED_STREAM_ENABLED: bool = True
    # Mirrors FRAME_HUB_IDLE_GRACE_SECONDS for the downstream annotated-output
    # publisher (ffmpeg -> mediamtx -> HLS): keeps its ffmpeg process and RTSP
    # publish connection alive for this long after the last viewer disconnects,
    # so a page reload reattaches to the still-running publisher instead of
    # tearing down and re-negotiating a fresh RTSP publish + HLS stream (the
    # dominant remaining cost behind the reload black screen once the camera
    # capture itself — see FRAME_HUB_IDLE_GRACE_SECONDS — is kept warm).
    # Cold-starting this chain leaves the HLS path 404ing for several
    # seconds, which the player can only sit and retry through, so this
    # window is kept generous enough that an ordinary reload always lands
    # inside it. A too-long window costs an idle encoder; a too-short one
    # costs a multi-second black screen on every reload.
    ANNOTATED_PUBLISHER_IDLE_GRACE_SECONDS: float = 60.0
    # How stale the poster still-frame served by /stream-snapshot may be.
    # It exists to cover the sub-second gap while a reloaded page starts its
    # HLS player, so anything beyond a few seconds is past its usefulness —
    # and serving an old frame as the current view of the factory floor
    # would be actively misleading. Beyond this age the endpoint 404s and
    # the player simply falls back to its black background.
    ANNOTATED_SNAPSHOT_MAX_AGE_SECONDS: float = 10.0
    ANNOTATED_STREAM_DELAY_SECONDS: float = 3.0
    ANNOTATED_STREAM_QUEUE_SIZE: int = 180
    ANNOTATED_PPE_TTL_FRAMES: int = 8
    ANNOTATED_SIGN_TTL_SECONDS: float = 3.0
    ANNOTATED_PPE_MATCH_IOU: float = 0.20
    ANNOTATED_RTSP_BASE_URL: str = "rtsp://127.0.0.1:8554"
    ANNOTATED_PATH_SUFFIX: str = "_annotated"
    ANNOTATED_FFMPEG_PATH: str = "ffmpeg"
    ANNOTATED_ENCODER: str = "auto"
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
    LIVE_SIGN_TARGET_FPS: float = 1.0
    LIVE_SIGN_PHASE_FRAME: int = 13
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
    # Behavioral-detection / behavior-incident settings.
    # Pose + behavior-classifier pipeline.  The classifier was trained on
    # 60-frame COCO-pose windows and predicts others/running/falling.
    FALL_MODEL_PATH: str = "weights/pose.pt"
    # Primary classifier: 141 transformed pose/motion features ->
    # others/running/falling. The current production artifact is an
    # sklearn ExtraTreesClassifier.
    FALL_BEHAVIOR_MODEL_PATH: str = "weights/best_behavior_model.joblib"
    FALL_REID_MODEL_PATH: str = "weights/reid.pt"
    FALL_PERSON_CONFIDENCE: float = 0.20
    FALL_BEHAVIOR_WINDOW_FRAMES: int = 60
    FALL_BEHAVIOR_WINDOW_STRIDE: int = 12
    FALL_BEHAVIOR_CANONICAL_FPS: int = 24
    FALL_BEHAVIOR_MIN_CONFIDENCE: float = 0.50
    FALL_TRACK_MAX_MISSING_SAMPLES: int = 12
    FALL_MAX_FRAMES: int = 1200
    FALL_FRAME_STRIDE: int = 1
    # Do not subsample pose frames: behavior.joblib was trained on 60 frames
    # at 24 FPS, so its temporal features require every source frame.
    FALL_LIVE_FRAME_STRIDE: int = 1
    # Multi-camera behavior runtime. Behavior frames remain ordered and are
    # never subsampled; batching only combines one ready frame per camera into
    # a single finite CUDA forward pass.
    BEHAVIOR_ORDERED_QUEUE_SIZE: int = 180
    BEHAVIOR_BATCH_MAX_SIZE: int = 4
    BEHAVIOR_CAMERA_BURST_SIZE: int = 2
    BEHAVIOR_BATCH_WAIT_MS: float = 4.0
    BEHAVIOR_POSE_IMGSZ: int = 448
    BEHAVIOR_FIXED_CAMERA: bool = True
    BEHAVIOR_GMC_METHOD: str = "none"
    BEHAVIOR_REID_HALF: bool = True
    BEHAVIOR_REID_INTERVAL_FRAMES: int = 4
    BEHAVIOR_POSE_REPAIR_MAX_GAP: int = 8
    BEHAVIOR_POSE_REPAIR_MIN_CONFIDENCE: float = 0.10
    BEHAVIOR_POSE_REPAIR_MAX_CENTER_SHIFT_RATIO: float = 1.50
    BEHAVIOR_LIVE_WARMUP_FRAMES: int = 3
    BEHAVIOR_START_COHORT_WAIT_MS: float = 1200.0
    BEHAVIOR_TORCH_THREADS: int = 4
    BEHAVIOR_TORCH_INTEROP_THREADS: int = 1
    BEHAVIOR_OPENCV_THREADS: int = 4
    BEHAVIOR_XGBOOST_THREADS: int = 1
    BEHAVIOR_HEALTH_LOG_INTERVAL_SECONDS: float = 10.0
    # Optional legacy XGBoost artifact. It is used only when the configured
    # primary classifier is unavailable.
    FALL_BEHAVIOR_PORTABLE_MODEL_PATH: str = "weights/behavior.ubj"
    FALL_INCIDENT_COOLDOWN_SECONDS: float = 10.0
    FALL_MODEL_NAME: str = "pose-behavior-xgboost"
    FALL_MODEL_VERSION: str = "behavior-v1"
    # Upper bound on rows UnifiedIncidentService will read per category per call.
    # Analytics aggregates in Python (see analytics_service.py's module docstring
    # for why), so a date range with more incidents than this gets its oldest
    # rows silently dropped from the counts. Raise this if real incident volume
    # approaches it; a truncation warning is logged when it's hit either way.
    ANALYTICS_LIMIT: int = 20000

    # ---- Reporting / PDF export ----
    # Display timezone for rendered report timestamps. All storage is UTC
    # (see UnifiedIncidentService._ensure_tz); this only affects presentation.
    REPORT_TIMEZONE: str = "Asia/Ho_Chi_Minh"
    REPORT_COMPANY_NAME: str = "De Heus LLC"
    REPORT_LOGO_PATH: str | None = None          # optional PNG/JPG, absolute or backend-relative
    REPORT_MAX_INCIDENT_ROWS: int = 25           # rows in the detail table
    REPORT_MAX_SNAPSHOTS: int = 6                # evidence thumbnails in the appendix
    REPORT_SNAPSHOT_TIMEOUT_SECONDS: float = 5.0 # per-image fetch budget
    REPORT_ARCHIVE_TO_MINIO: bool = True

    # ---- SMTP ----
    REPORT_EMAIL_ENABLED: bool = False           # master switch; see security note in the plan
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_USE_STARTTLS: bool = True               # port 587
    SMTP_USE_SSL: bool = False                   # port 465; mutually exclusive with STARTTLS
    SMTP_TIMEOUT_SECONDS: float = 20.0
    SMTP_FROM_EMAIL: str | None = None
    SMTP_FROM_NAME: str = "Smart Factory Safety Monitoring"
    # Empty list = allow any recipient. NON-EMPTY IS STRONGLY RECOMMENDED: without
    # auth on this API, an open recipient field makes /reports/incidents/email a
    # spam relay. Exact-match emails and/or "@domain.com" suffixes are accepted.
    REPORT_RECIPIENT_ALLOWLIST: list[str] = []
    REPORT_MAX_RECIPIENTS: int = 10
    # In-process rate limit for POST /reports/incidents/email. Does not survive
    # multi-worker uvicorn (each worker keeps its own counter).
    REPORT_EMAIL_RATE_LIMIT_PER_HOUR: int = 20

    @model_validator(mode="after")
    def _coerce_sign_dict_keys(self) -> "Settings":
        self.SIGN_CLASS_ZONE_MAP = {int(k): v for k, v in self.SIGN_CLASS_ZONE_MAP.items()}
        self.SIGN_CLASS_NAMES = {int(k): v for k, v in self.SIGN_CLASS_NAMES.items()}
        self.SIGN_CLASS_PPE_TRIGGER = {int(k) for k in self.SIGN_CLASS_PPE_TRIGGER}
        return self

    @model_validator(mode="after")
    def _validate_smtp(self) -> "Settings":
        if self.SMTP_USE_SSL and self.SMTP_USE_STARTTLS:
            raise ValueError("SMTP_USE_SSL and SMTP_USE_STARTTLS are mutually exclusive.")
        return self

    @model_validator(mode="after")
    def _validate_behavior_runtime(self) -> "Settings":
        positive_ints = {
            "FALL_BEHAVIOR_WINDOW_FRAMES": self.FALL_BEHAVIOR_WINDOW_FRAMES,
            "FALL_BEHAVIOR_WINDOW_STRIDE": self.FALL_BEHAVIOR_WINDOW_STRIDE,
            "FALL_BEHAVIOR_CANONICAL_FPS": self.FALL_BEHAVIOR_CANONICAL_FPS,
            "FALL_LIVE_FRAME_STRIDE": self.FALL_LIVE_FRAME_STRIDE,
            "BEHAVIOR_ORDERED_QUEUE_SIZE": self.BEHAVIOR_ORDERED_QUEUE_SIZE,
            "BEHAVIOR_BATCH_MAX_SIZE": self.BEHAVIOR_BATCH_MAX_SIZE,
            "BEHAVIOR_CAMERA_BURST_SIZE": self.BEHAVIOR_CAMERA_BURST_SIZE,
            "BEHAVIOR_POSE_IMGSZ": self.BEHAVIOR_POSE_IMGSZ,
            "BEHAVIOR_TORCH_THREADS": self.BEHAVIOR_TORCH_THREADS,
            "BEHAVIOR_TORCH_INTEROP_THREADS": self.BEHAVIOR_TORCH_INTEROP_THREADS,
            "BEHAVIOR_OPENCV_THREADS": self.BEHAVIOR_OPENCV_THREADS,
            "BEHAVIOR_XGBOOST_THREADS": self.BEHAVIOR_XGBOOST_THREADS,
            "BEHAVIOR_REID_INTERVAL_FRAMES": self.BEHAVIOR_REID_INTERVAL_FRAMES,
            "BEHAVIOR_POSE_REPAIR_MAX_GAP": self.BEHAVIOR_POSE_REPAIR_MAX_GAP,
            "BEHAVIOR_LIVE_WARMUP_FRAMES": self.BEHAVIOR_LIVE_WARMUP_FRAMES,
            "ANNOTATED_STREAM_QUEUE_SIZE": self.ANNOTATED_STREAM_QUEUE_SIZE,
            "ANNOTATED_PPE_TTL_FRAMES": self.ANNOTATED_PPE_TTL_FRAMES,
        }
        invalid = [name for name, value in positive_ints.items() if value < 1]
        if invalid:
            raise ValueError(f"Behavior runtime values must be positive: {', '.join(invalid)}")
        if self.BEHAVIOR_CAMERA_BURST_SIZE > self.BEHAVIOR_BATCH_MAX_SIZE:
            raise ValueError(
                "BEHAVIOR_CAMERA_BURST_SIZE cannot exceed BEHAVIOR_BATCH_MAX_SIZE."
            )
        if self.FALL_LIVE_FRAME_STRIDE != 1:
            raise ValueError(
                "FALL_LIVE_FRAME_STRIDE must remain 1; behavior.joblib requires "
                "ordered 24-FPS temporal samples."
            )
        if self.BEHAVIOR_BATCH_WAIT_MS < 0:
            raise ValueError("BEHAVIOR_BATCH_WAIT_MS cannot be negative.")
        if self.BEHAVIOR_START_COHORT_WAIT_MS < 0:
            raise ValueError("BEHAVIOR_START_COHORT_WAIT_MS cannot be negative.")
        if self.BEHAVIOR_HEALTH_LOG_INTERVAL_SECONDS <= 0:
            raise ValueError("BEHAVIOR_HEALTH_LOG_INTERVAL_SECONDS must be positive.")
        if self.LIVE_PPE_TARGET_FPS <= 0 or self.LIVE_SIGN_TARGET_FPS <= 0:
            raise ValueError("Live model target FPS values must be positive.")
        if self.ANNOTATED_STREAM_DELAY_SECONDS < 0:
            raise ValueError("ANNOTATED_STREAM_DELAY_SECONDS cannot be negative.")
        if self.ANNOTATED_SIGN_TTL_SECONDS <= 0:
            raise ValueError("ANNOTATED_SIGN_TTL_SECONDS must be positive.")
        if not 0 <= self.ANNOTATED_PPE_MATCH_IOU <= 1:
            raise ValueError("ANNOTATED_PPE_MATCH_IOU must be between 0 and 1.")
        if self.ANNOTATED_ENCODER not in {"auto", "h264_nvenc", "libx264"}:
            raise ValueError(
                "ANNOTATED_ENCODER must be auto, h264_nvenc, or libx264."
            )
        if not self.ANNOTATED_PATH_SUFFIX or "/" in self.ANNOTATED_PATH_SUFFIX:
            raise ValueError("ANNOTATED_PATH_SUFFIX must be a non-empty path suffix.")
        if self.LIVE_SIGN_PHASE_FRAME < 0:
            raise ValueError("LIVE_SIGN_PHASE_FRAME cannot be negative.")
        if not 0 <= self.BEHAVIOR_POSE_REPAIR_MIN_CONFIDENCE <= 1:
            raise ValueError(
                "BEHAVIOR_POSE_REPAIR_MIN_CONFIDENCE must be between 0 and 1."
            )
        if self.BEHAVIOR_POSE_REPAIR_MAX_CENTER_SHIFT_RATIO <= 0:
            raise ValueError(
                "BEHAVIOR_POSE_REPAIR_MAX_CENTER_SHIFT_RATIO must be positive."
            )
        allowed_gmc = {"none", "orb", "sift", "ecc", "sparseOptFlow"}
        if self.BEHAVIOR_GMC_METHOD not in allowed_gmc:
            raise ValueError(
                f"BEHAVIOR_GMC_METHOD must be one of {sorted(allowed_gmc)}."
            )
        if self.BEHAVIOR_FIXED_CAMERA:
            self.BEHAVIOR_GMC_METHOD = "none"
        return self

    class Config:
        env_file = str(BACKEND_DIR / ".env")


settings = Settings()
