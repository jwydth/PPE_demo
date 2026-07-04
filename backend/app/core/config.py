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
    # Separate, longer gap tolerance specifically for the fall state machine.
    # Multiple people overlapping/occluding each other mid-fall can cause a
    # tracked person to disappear from detection for a second or more even
    # though they never left the scene -- resetting on the same short window
    # tuned for routine walkway dwell (VIDEO_ZONE_REENTRY_GAP_SECONDS) would
    # wipe out floor-time accumulation right as it's about to confirm.
    FALL_REENTRY_GAP_SECONDS: float = 3.0
    # When zone monitoring is enabled but no WALKWAY zone is defined, every
    # detected walker is treated as being outside a walkway.  A violation is
    # raised once the worker has been visible for this many consecutive seconds.
    NO_WALKWAY_DWELL_SECONDS: float = 1.5
    # Fall detection (pure bounding-box geometry, no pose estimation)
    FALL_ENABLED: bool = True
    FALL_ASPECT_RATIO_THRESHOLD: float = 1.1  # w/h above this = "lying" shape
    FALL_STANDING_RATIO: float = 0.7  # w/h below this = "standing" shape
    FALL_VERTICAL_VELOCITY_THRESHOLD: float = 0.12  # normalized (frac of frame height) per second
    FALL_CONFIRM_SECONDS: float = 1.0  # must stay "lying" this long to confirm a fall
    FALL_MIN_BOX_AREA_FRAC: float = 0.005  # ignore tiny/distant boxes (fraction of frame area)
    FALL_WINDOW_FRAMES: int = 8  # rolling box history length for velocity
    # How long a single noisy/dropped frame is allowed to look "not lying" before
    # the state machine gives up on the fall and resets to standing.
    FALL_LYING_GRACE_SECONDS: float = 0.5
    # Cap on how much a *single* observation can contribute toward that grace
    # timer, regardless of the true elapsed gap since the last observation.
    # Without this, a multi-frame detection gap (a few frames of missed/noisy
    # detection while the person hasn't actually moved) followed by one
    # borderline "looks recovered" reading can single-handedly cross the full
    # grace threshold in one step -- one noisy sample deciding the outcome
    # instead of sustained evidence across several. This forces at least a
    # few separate observations to agree before resetting to standing.
    FALL_LYING_MISS_STEP_CAP_SECONDS: float = 0.2
    # After resetting from falling/lying back to standing, how long the person
    # must look genuinely recovered (height back near the pre-fall baseline)
    # before the standing-height baseline is allowed to adapt again. Without
    # this, a brief box-size blip that triggers a premature reset (e.g. a limb
    # shifting while still on the ground) lets the baseline immediately
    # re-anchor to that still-on-the-ground height, permanently hiding any
    # later, deeper collapse measured against it.
    FALL_RECOVERY_CONFIRM_SECONDS: float = 1.0
    # Looser spatial re-match thresholds used only when the candidate worker is
    # currently "falling"/"lying" — the fall motion itself is what most often
    # breaks ByteTrack's ID continuity, so a track-ID switch during exactly that
    # window needs a more forgiving IOU/center-distance bar to reattach.
    FALL_REMATCH_IOU_THRESHOLD: float = 0.05
    FALL_REMATCH_CENTER_DISTANCE_RATIO: float = 1.5
    # A fall onto the buttocks (or otherwise ending upright-ish) never produces
    # a wide/flat box, so aspect ratio alone misses it. This catches it via a
    # sudden collapse in box height relative to the person's own recent
    # standing height instead — fires alongside, not instead of, the aspect
    # ratio signal.
    FALL_HEIGHT_DROP_RATIO_THRESHOLD: float = 0.30  # fraction of standing height suddenly lost
    FALL_HEIGHT_BASELINE_ALPHA: float = 0.15  # EMA smoothing for the rolling standing-height baseline
    # How much height-drop is tolerated before the baseline stops adapting,
    # deliberately much smaller than FALL_HEIGHT_DROP_RATIO_THRESHOLD. A fall
    # preceded by a slow, deliberate crouch (e.g. bending down to inspect a
    # spill before slipping) shrinks the box gradually across many frames,
    # none of which individually cross the 30% entry bar -- so without an
    # earlier freeze, the baseline keeps chasing the crouch down and the
    # *eventual* fall-from-crouch never reads as a big enough additional drop
    # once it happens. Trade-off: a person who legitimately just walks
    # farther from the camera also freezes early and reads as increasingly
    # "collapsed" the farther they go, so this raises false-trigger risk for
    # ordinary walking away from the lens.
    FALL_BASELINE_FREEZE_RATIO: float = 0.12
    # A height loss this extreme bypasses the velocity gate entirely. Some
    # falls collapse straight down (buttocks-first) rather than toppling, so
    # the box centroid barely moves even while height crashes — velocity is
    # simply the wrong signal for that motion, no matter how it's measured.
    # A deliberate crouch/kneel can plausibly reach the base
    # FALL_HEIGHT_DROP_RATIO_THRESHOLD, but losing this much more height is a
    # stronger, less ambiguous signal on its own. The sustained-duration
    # confirm window (FALL_CONFIRM_SECONDS) still filters out momentary noise.
    FALL_HARD_HEIGHT_DROP_RATIO: float = 0.40

    @model_validator(mode="after")
    def _coerce_sign_dict_keys(self) -> "Settings":
        self.SIGN_CLASS_ZONE_MAP = {int(k): v for k, v in self.SIGN_CLASS_ZONE_MAP.items()}
        self.SIGN_CLASS_NAMES = {int(k): v for k, v in self.SIGN_CLASS_NAMES.items()}
        self.SIGN_CLASS_PPE_TRIGGER = {int(k) for k in self.SIGN_CLASS_PPE_TRIGGER}
        return self

    class Config:
        env_file = str(BACKEND_DIR / ".env")


settings = Settings()
