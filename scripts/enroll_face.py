"""
Face Enrollment Script
Captures multiple photos of owner face for recognition training.
"""
import cv2
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.capture.webcam import WebcamCapture
from src.detection.face_detector import FaceDetector
from src.detection.face_recognizer import FaceRecognizer
from src.config import config


def main():
    print("=" * 50)
    print("   PrankGuard - Face Enrollment")
    print("=" * 50)
    print()

    num_photos = 50
    print(f"This will capture {num_photos} photos of your face.")
    print("Move your head slightly between captures for variety.")
    print()
    print("Press [SPACE] to capture, [Q] to finish early, [R] to reset")
    print()

    # Initialize
    webcam = WebcamCapture(config.CAMERA_INDEX)
    detector = FaceDetector()
    recognizer = FaceRecognizer(config.OWNER_FACES_DIR)

    if not webcam.start(config.FRAME_WIDTH, config.FRAME_HEIGHT, 30):
        print("❌ Failed to open webcam")
        return 1

    time.sleep(1)  # Let camera warm up

    captured = 0
    photo_index = len(list(config.OWNER_FACES_DIR.glob("*.jpg")))

    cv2.namedWindow("Enrollment", cv2.WINDOW_NORMAL)

    print(f"\nExisting photos: {photo_index}")
    print("Ready! Position your face in the frame.\n")

    while captured < num_photos:
        frame = webcam.get_frame()
        if frame is None:
            continue

        display = frame.copy()
        faces = detector.detect(frame)

        # Draw face boxes
        for face in faces:
            x, y, w, h = face.bbox
            cv2.rectangle(display, (x, y), (x + w, y + h), (0, 255, 0), 2)

            # Show head pose
            cv2.putText(
                display,
                f"Yaw:{face.yaw:.0f} Pitch:{face.pitch:.0f}",
                (x, y - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                1
            )

        # Status
        cv2.putText(
            display,
            f"Captured: {captured}/{num_photos} | SPACE=capture Q=quit R=reset",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            2
        )

        if len(faces) == 0:
            cv2.putText(
                display,
                "No face detected",
                (10, 60),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 0, 255),
                2
            )
        elif len(faces) > 1:
            cv2.putText(
                display,
                "Multiple faces - ensure only you are visible",
                (10, 60),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 165, 255),
                2
            )

        cv2.imshow("Enrollment", display)
        key = cv2.waitKey(1) & 0xFF

        if key == ord(" ") and len(faces) == 1:
            # Save photo
            face = faces[0]
            photo_path = config.OWNER_FACES_DIR / f"owner_{photo_index:03d}.jpg"

            # Crop face with margin
            x, y, w, h = face.bbox
            margin = int(0.3 * max(w, h))
            y1 = max(0, y - margin)
            y2 = min(frame.shape[0], y + h + margin)
            x1 = max(0, x - margin)
            x2 = min(frame.shape[1], x + w + margin)

            face_img = frame[y1:y2, x1:x2]
            cv2.imwrite(str(photo_path), face_img)

            # Also add encoding directly
            recognizer.add_owner_encoding(frame, face.bbox)

            captured += 1
            photo_index += 1
            print(f"📸 Captured {captured}/{num_photos}")

            # Flash effect
            cv2.rectangle(display, (0, 0), (display.shape[1], display.shape[0]), (255, 255, 255), -1)
            cv2.imshow("Enrollment", display)
            cv2.waitKey(100)

        elif key == ord("q"):
            break

        elif key == ord("r"):
            # Reset
            recognizer.clear_encodings()
            for f in config.OWNER_FACES_DIR.glob("*.jpg"):
                f.unlink()
            captured = 0
            photo_index = 0
            print("🗑️  Reset - all photos cleared")

    cv2.destroyAllWindows()
    webcam.stop()
    detector.close()

    print()
    print("=" * 50)
    print(f"✅ Enrollment complete! {captured} photos captured.")
    print(f"   Total encodings: {len(recognizer.owner_encodings)}")
    print("=" * 50)

    return 0


if __name__ == "__main__":
    sys.exit(main())
