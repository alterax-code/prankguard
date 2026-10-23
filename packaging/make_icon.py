"""Génère packaging/prankguard.ico à partir du bouclier du systray (multi-résolutions)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from prankguard.tray import shield_icon  # noqa: E402

out = Path(__file__).with_name("prankguard.ico")
img = shield_icon((34, 197, 94), 256)
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(out)
