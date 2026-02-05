import keyboard
import time
import sys
from src.core import Guardian, GuardianState


def print_status(state: GuardianState):
    status_map = {
        GuardianState.IDLE: "😴 Idle - No face detected",
        GuardianState.OWNER_ACTIVE: "✅ Owner active",
        GuardianState.SUSPECT: "⚠️  SUSPECT - Unknown face looking!",
        GuardianState.SHOULDER_SURF: "👀 Shoulder surfer detected!",
        GuardianState.LOCKING: "🔒 LOCKING...",
        GuardianState.COOLDOWN: "⏳ Cooldown..."
    }
    print(f"\r{status_map.get(state, state.name):<50}", end="", flush=True)


def on_alert(message: str):
    print(f"\n🚨 ALERT: {message}")


def main():
    print("=" * 50)
    print("   PrankGuard - Anti-Prank Screen Locker")
    print("=" * 50)
    print()

    guardian = Guardian(
        on_state_change=print_status,
        on_alert=on_alert
    )

    if not guardian.is_enrolled:
        print("❌ No owner face enrolled!")
        print("Run: python scripts/enroll_face.py")
        return 1

    print(f"✅ Owner enrolled ({len(guardian.face_recognizer.owner_encodings)} faces)")
    print()
    print("Controls:")
    print("  [P] Pause/Resume")
    print("  [Q] Quit")
    print()

    if not guardian.start():
        print("❌ Failed to start (check webcam)")
        return 1

    print("🎬 Monitoring started...")
    print()

    try:
        while True:
            if keyboard.is_pressed("p"):
                if guardian.is_paused:
                    guardian.resume()
                else:
                    guardian.pause()
                time.sleep(0.3)  # Debounce

            if keyboard.is_pressed("q"):
                print("\n\nQuitting...")
                break

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n\nInterrupted...")

    finally:
        guardian.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
