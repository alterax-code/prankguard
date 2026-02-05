"""
Calibration Script
Test and visualize face detection, recognition, and attention analysis.
"""
import cv2
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.capture.webcam import WebcamCapture
from src.detection.face_detector import FaceDetector
from src.detection.face_recognizer import FaceRecognizer
from src.detection.attention_analyzer import AttentionAnalyzer
from src.config import config


def main():
    print("=" * 50)
    print("   PrankGuard - Calibration & Debug")
    print("=" * 50)
    print()
    print("This shows real-time detection values.")
    print("Use to tune thresholds in config.py")
    print()
    print("Press [Q] to quit")
    print()

    webcam = WebcamCapture(config.CAMERA_INDEX)
    detector = FaceDetector()
    recognizer = FaceRecognizer(config.OWNER_FACES_DIR)
    analyzer = AttentionAnalyzer(
        yaw_threshold=config.HEAD_YAW_THRESHOLD,
        pitch_threshold=config.HEAD_PITCH_THRESHOLD,
        center_threshold=config.FACE_CENTER_THRESHOLD,
        min_face_size=config.MIN_FACE_SIZE_RATIO
    )

    if not webcam.start(config.FRAME_WIDTH, config.FRAME_HEIGHT, 30):
        print("❌ Failed to open webcam")
        return 1

    time.sleep(1)

    cv2.namedWindow("Calibration", cv2.WINDOW_NORMAL)

    print(f"Owner enrolled: {recognizer.is_enrolled}")
    print(f"Encodings: {len(recognizer.owner_encodings)}")
    print()

    while True:
        frame = webcam.get_frame()
        if frame is None:
            continue

        display = frame.copy()
        faces = detector.detect(frame)

        y_offset = 30

        for i, face in enumerate(faces):
            x, y, w, h = face.bbox

            # Check if owner
            is_owner = recognizer.is_owner(frame, face.bbox)

            # Analyze attention
            attention = analyzer.analyze(face)

            # Color based on identity
            if is_owner is True:
                color = (0, 255, 0)  # Green = owner
                label = "OWNER"
            elif is_owner is False:
                color = (0, 0, 255)  # Red = stranger
                label = "STRANGER"
            else:
                color = (0, 165, 255)  # Orange = unknown
                label = "UNKNOWN"

            # Draw box
            cv2.rectangle(display, (x, y), (x + w, y + h), color, 2)
            cv2.putText(display, label, (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

            # Attention indicator
            if attention.is_looking_at_screen:
                cv2.putText(display, "LOOKING AT SCREEN", (x, y + h + 20),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)

            # Debug info panel
            info_lines = [
                f"Face {i + 1}:",
                f"  Yaw: {face.yaw:+6.1f} (thresh: {config.HEAD_YAW_THRESHOLD})",
                f"  Pitch: {face.pitch:+6.1f} (thresh: {config.HEAD_PITCH_THRESHOLD})",
                f"  Size: {face.size_ratio:.2%} (min: {config.MIN_FACE_SIZE_RATIO:.0%})",
                f"  Center: ({face.center_offset[0]:+.2f}, {face.center_offset[1]:+.2f})",
                f"  Attention: {attention.reason}",
                f"  Confidence: {attention.confidence:.0%}",
                ""
            ]

            for line in info_lines:
                cv2.putText(display, line, (10, y_offset),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                y_offset += 20

        # Thresholds reference
        ref_y = display.shape[0] - 80
        cv2.putText(display, "Thresholds (config.py):", (10, ref_y),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        cv2.putText(display, f"  Yaw: {config.HEAD_YAW_THRESHOLD} | Pitch: {config.HEAD_PITCH_THRESHOLD}",
                   (10, ref_y + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        cv2.putText(display, f"  Min Size: {config.MIN_FACE_SIZE_RATIO:.0%} | Tolerance: {config.FACE_RECOGNITION_TOLERANCE}",
                   (10, ref_y + 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

        cv2.imshow("Calibration", display)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cv2.destroyAllWindows()
    webcam.stop()
    detector.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
