from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    # Paths
    BASE_DIR: Path = field(default_factory=lambda: Path(__file__).parent.parent)
    DATA_DIR: Path = field(default_factory=lambda: Path(__file__).parent.parent / "data")
    OWNER_FACES_DIR: Path = field(default_factory=lambda: Path(__file__).parent.parent / "data" / "owner_faces")

    # Camera
    CAMERA_INDEX: int = 0
    FRAME_WIDTH: int = 640
    FRAME_HEIGHT: int = 480
    FPS: int = 15

    # Face Recognition
    FACE_RECOGNITION_TOLERANCE: float = 0.6  # Lower = stricter (faux positifs préférés)
    MIN_FACE_SIZE_RATIO: float = 0.10  # Face must be at least 10% of frame height

    # Attention Detection
    HEAD_YAW_THRESHOLD: float = 25.0  # Degrees - max horizontal rotation
    HEAD_PITCH_THRESHOLD: float = 20.0  # Degrees - max vertical rotation
    FACE_CENTER_THRESHOLD: float = 0.3  # Face must be within 30% of center

    # Timing
    ATTENTION_DURATION_THRESHOLD: float = 1.5  # Seconds before lock
    GRACE_PERIOD: float = 0.5  # Seconds to confirm before locking
    COOLDOWN_AFTER_LOCK: float = 3.0  # Seconds to wait after locking

    # Behavior
    LOCK_ON_UNKNOWN: bool = True
    ALERT_ON_SHOULDER_SURF: bool = True  # Alert when owner + stranger

    def __post_init__(self):
        self.OWNER_FACES_DIR.mkdir(parents=True, exist_ok=True)


config = Config()
