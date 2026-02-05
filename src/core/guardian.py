import time
import threading
import winsound
from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional, List, Callable

from ..capture.webcam import WebcamCapture
from ..detection.face_detector import FaceDetector, FaceData
from ..detection.face_recognizer import FaceRecognizer
from ..detection.attention_analyzer import AttentionAnalyzer, AttentionState
from ..security.locker import WindowsLocker
from ..config import config


class GuardianState(Enum):
    IDLE = auto()          # No face detected
    OWNER_ACTIVE = auto()  # Owner is using PC
    SUSPECT = auto()       # Unknown face looking at screen
    SHOULDER_SURF = auto() # Owner + stranger detected
    LOCKING = auto()       # About to lock
    COOLDOWN = auto()      # Just locked, waiting


@dataclass
class FrameAnalysis:
    """Result of analyzing a single frame."""
    faces: List[FaceData]
    owner_present: bool
    stranger_looking: bool
    stranger_faces: List[FaceData]


class Guardian:
    """Main orchestrator - monitors webcam and locks on intruders."""

    def __init__(
        self,
        on_state_change: Optional[Callable[[GuardianState], None]] = None,
        on_alert: Optional[Callable[[str], None]] = None
    ):
        self.webcam = WebcamCapture(config.CAMERA_INDEX)
        self.face_detector = FaceDetector()
        self.face_recognizer = FaceRecognizer(
            config.OWNER_FACES_DIR,
            tolerance=config.FACE_RECOGNITION_TOLERANCE
        )
        self.attention_analyzer = AttentionAnalyzer(
            yaw_threshold=config.HEAD_YAW_THRESHOLD,
            pitch_threshold=config.HEAD_PITCH_THRESHOLD,
            center_threshold=config.FACE_CENTER_THRESHOLD,
            min_face_size=config.MIN_FACE_SIZE_RATIO
        )
        self.locker = WindowsLocker()
        self.locker.set_cooldown(config.COOLDOWN_AFTER_LOCK)

        self._state = GuardianState.IDLE
        self._suspect_start_time: Optional[float] = None
        self._running = False
        self._paused = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self.on_state_change = on_state_change
        self.on_alert = on_alert

    @property
    def state(self) -> GuardianState:
        return self._state

    @property
    def is_enrolled(self) -> bool:
        return self.face_recognizer.is_enrolled

    def _set_state(self, new_state: GuardianState):
        if new_state != self._state:
            old_state = self._state
            self._state = new_state
            print(f"[Guardian] State: {old_state.name} -> {new_state.name}")
            if self.on_state_change:
                self.on_state_change(new_state)

    def _analyze_frame(self, frame) -> Optional[FrameAnalysis]:
        if frame is None:
            return None

        faces = self.face_detector.detect(frame)
        if not faces:
            return FrameAnalysis(
                faces=[],
                owner_present=False,
                stranger_looking=False,
                stranger_faces=[]
            )

        owner_present = False
        stranger_faces = []
        stranger_looking = False

        for face in faces:
            is_owner = self.face_recognizer.is_owner(frame, face.bbox)

            if is_owner is True:
                owner_present = True
            elif is_owner is False:
                stranger_faces.append(face)
                # Check if stranger is looking at screen
                attention = self.attention_analyzer.analyze(face)
                if attention.is_looking_at_screen:
                    stranger_looking = True

        return FrameAnalysis(
            faces=faces,
            owner_present=owner_present,
            stranger_looking=stranger_looking,
            stranger_faces=stranger_faces
        )

    def _alert(self, message: str):
        """Trigger alert (beep + callback)."""
        print(f"[ALERT] {message}")
        try:
            winsound.Beep(1000, 200)  # 1000Hz for 200ms
        except:
            pass
        if self.on_alert:
            self.on_alert(message)

    def _process_cycle(self):
        frame = self.webcam.get_frame()
        analysis = self._analyze_frame(frame)

        if analysis is None:
            return

        current_time = time.time()

        # State machine logic
        if self._state == GuardianState.COOLDOWN:
            # Wait after lock
            if current_time - self._suspect_start_time >= config.COOLDOWN_AFTER_LOCK:
                self._set_state(GuardianState.IDLE)
            return

        if not analysis.faces:
            # No face detected
            self._set_state(GuardianState.IDLE)
            self._suspect_start_time = None
            return

        if analysis.owner_present and analysis.stranger_faces:
            # Owner + stranger = shoulder surfing alert
            if config.ALERT_ON_SHOULDER_SURF:
                if self._state != GuardianState.SHOULDER_SURF:
                    self._alert("Shoulder surfer detected!")
                self._set_state(GuardianState.SHOULDER_SURF)
            return

        if analysis.owner_present:
            # Only owner - all good
            self._set_state(GuardianState.OWNER_ACTIVE)
            self._suspect_start_time = None
            return

        if analysis.stranger_looking:
            # Stranger looking at screen!
            if self._state != GuardianState.SUSPECT:
                self._set_state(GuardianState.SUSPECT)
                self._suspect_start_time = current_time
            else:
                # Check if threshold reached
                elapsed = current_time - self._suspect_start_time
                if elapsed >= config.ATTENTION_DURATION_THRESHOLD:
                    self._set_state(GuardianState.LOCKING)
                    print(f"[Guardian] LOCKING - Stranger looked for {elapsed:.1f}s")
                    if self.locker.lock():
                        self._suspect_start_time = current_time
                        self._set_state(GuardianState.COOLDOWN)
        else:
            # Stranger present but not looking
            self._set_state(GuardianState.IDLE)
            self._suspect_start_time = None

    def _run_loop(self):
        while self._running:
            if not self._paused:
                with self._lock:
                    self._process_cycle()
            time.sleep(1.0 / config.FPS)

    def start(self) -> bool:
        if self._running:
            return True

        if not self.webcam.start(
            config.FRAME_WIDTH,
            config.FRAME_HEIGHT,
            config.FPS
        ):
            print("[Guardian] Failed to start webcam")
            return False

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        print("[Guardian] Started monitoring")
        return True

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        self.webcam.stop()
        self.face_detector.close()
        print("[Guardian] Stopped")

    def pause(self):
        self._paused = True
        print("[Guardian] Paused")

    def resume(self):
        self._paused = False
        print("[Guardian] Resumed")

    @property
    def is_paused(self) -> bool:
        return self._paused
