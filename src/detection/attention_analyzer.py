from dataclasses import dataclass
from typing import Optional
from .face_detector import FaceData


@dataclass
class AttentionState:
    """Attention analysis result."""
    is_looking_at_screen: bool
    confidence: float  # 0.0 to 1.0
    reason: str


class AttentionAnalyzer:
    """Analyzes if a person is actively looking at/using the screen."""

    def __init__(
        self,
        yaw_threshold: float = 25.0,
        pitch_threshold: float = 20.0,
        center_threshold: float = 0.3,
        min_face_size: float = 0.10
    ):
        self.yaw_threshold = yaw_threshold
        self.pitch_threshold = pitch_threshold
        self.center_threshold = center_threshold
        self.min_face_size = min_face_size

    def analyze(self, face: FaceData) -> AttentionState:
        """
        Determine if the face is actively looking at the screen.

        Criteria:
        - Face is roughly centered (not just passing by)
        - Face is large enough (close to screen)
        - Head is oriented toward screen (yaw/pitch within threshold)
        """
        reasons = []
        scores = []

        # Check face size (is person close enough?)
        if face.size_ratio < self.min_face_size:
            return AttentionState(
                is_looking_at_screen=False,
                confidence=1.0,
                reason="too_far"
            )

        size_score = min(1.0, face.size_ratio / 0.25)
        scores.append(size_score)

        # Check if face is centered
        center_dist = (face.center_offset[0] ** 2 + face.center_offset[1] ** 2) ** 0.5
        is_centered = center_dist <= self.center_threshold
        center_score = max(0.0, 1.0 - center_dist / self.center_threshold)
        scores.append(center_score)

        if not is_centered:
            reasons.append("off_center")

        # Check head orientation
        yaw_ok = abs(face.yaw) <= self.yaw_threshold
        pitch_ok = abs(face.pitch) <= self.pitch_threshold

        yaw_score = max(0.0, 1.0 - abs(face.yaw) / self.yaw_threshold)
        pitch_score = max(0.0, 1.0 - abs(face.pitch) / self.pitch_threshold)
        scores.append(yaw_score)
        scores.append(pitch_score)

        if not yaw_ok:
            reasons.append("looking_away_horizontal")
        if not pitch_ok:
            reasons.append("looking_away_vertical")

        # Overall assessment
        is_looking = is_centered and yaw_ok and pitch_ok
        confidence = sum(scores) / len(scores)

        reason = "looking_at_screen" if is_looking else ",".join(reasons)

        return AttentionState(
            is_looking_at_screen=is_looking,
            confidence=confidence,
            reason=reason
        )
