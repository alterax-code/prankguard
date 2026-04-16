"""
Fenêtre d'enrollment facial.
FIX 10 — Minimum 15 photos, tips dynamiques, barre de progression sur 30.
"""
import io
import os
import cv2
import face_recognition
import numpy as np
import time
import threading
import winsound
from typing import Callable, Optional

import customtkinter as ctk
from PIL import Image


# Tips qui changent pendant la capture
CAPTURE_TIPS = [
    "Regardez droit vers la caméra",
    "Tournez légèrement la tête à gauche",
    "Tournez légèrement la tête à droite",
    "Levez légèrement le menton",
    "Baissez légèrement le menton",
    "Souriez !",
    "Avec lunettes (si applicable)",
    "Sans lunettes (si applicable)",
    "Éclairage différent — tournez-vous",
    "Expression neutre",
    "Reculez un peu",
    "Rapprochez-vous",
    "Inclinez la tête à gauche",
    "Inclinez la tête à droite",
    "Fermez les yeux puis rouvrez",
]

MIN_PHOTOS = 15
OPTIMAL_PHOTOS = 30


def check_enrollment(encodings_path: str) -> bool:
    """Vérifie si un enrollment valide existe (supporte .npy, .npz et fichiers chiffrés)."""
    if not os.path.exists(encodings_path):
        return False
    try:
        from src.crypto import is_encrypted, decrypt_encodings
        raw = open(encodings_path, "rb").read()
        if is_encrypted(raw):
            raw = decrypt_encodings(raw)
        data = np.load(io.BytesIO(raw), allow_pickle=False)
        return any(len(data[k]) > 0 for k in data.files)
    except Exception:
        return False


def load_authorized_users(path: str) -> dict:
    """
    Charge les utilisateurs depuis un fichier .npz.
    Retourne {nom: ndarray(N, 128)}.
    """
    data = np.load(path, allow_pickle=False)
    return {name: data[name] for name in data.files}


def load_authorized_users_from_bytes(raw: bytes) -> dict:
    """
    Charge les utilisateurs depuis des bytes (après déchiffrement en mémoire).
    Retourne {nom: ndarray(N, 128)}.
    """
    buf = io.BytesIO(raw)
    data = np.load(buf, allow_pickle=False)
    return {name: data[name] for name in data.files}


def save_authorized_users(path: str, users: dict) -> None:
    """
    Sauvegarde {nom: ndarray(N, 128)} dans un fichier .npz.
    Crée les répertoires parents si nécessaire.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    np.savez(path, **users)


class EnrollmentWindow(ctk.CTkToplevel):
    """Fenêtre de capture de visages pour l'enrollment d'un utilisateur."""

    def __init__(
        self,
        parent,
        encodings_path: str,
        username: str = "owner",
        encrypt_enabled: bool = False,
        on_success: Optional[Callable] = None,
        on_cancel: Optional[Callable] = None,
    ):
        super().__init__(parent)
        self.encodings_path = encodings_path
        self.username = username
        self.encrypt_enabled = encrypt_enabled
        self.on_success = on_success
        self.on_cancel = on_cancel

        self.title(f"PrankGuard — Enrollment ({username})")
        self.geometry("720x600")
        self.resizable(False, False)
        self.grab_set()  # Modal — focus exclusif sur cette fenêtre

        self.encodings = []
        self.cap = None
        self.running = True
        self._closing = False  # Guard contre les callbacks .after() post-destroy
        self.photo_count = 0
        self.current_frame = None

        # Charger les encodings existants pour cet utilisateur si présents
        if os.path.exists(self.encodings_path):
            try:
                from src.crypto import is_encrypted, decrypt_encodings
                raw = open(self.encodings_path, "rb").read()
                if is_encrypted(raw):
                    raw = decrypt_encodings(raw)
                data = np.load(io.BytesIO(raw), allow_pickle=False)
                if username in data.files:
                    self.encodings = list(data[username])
                self.photo_count = len(self.encodings)
            except Exception:
                pass

        self._build_ui()

        # Lancer la caméra
        threading.Thread(target=self._cam_loop, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        """Construit l'interface d'enrollment."""
        ctk.CTkLabel(
            self, text="PrankGuard — Enrollment",
            font=ctk.CTkFont(size=26, weight="bold")
        ).pack(pady=(15, 5))

        # Tip dynamique (FIX 10)
        self.tip_label = ctk.CTkLabel(
            self, text=CAPTURE_TIPS[0],
            font=ctk.CTkFont(size=13), text_color="#f39c12"
        )
        self.tip_label.pack(pady=5)

        # Zone caméra
        self.cam_frame = ctk.CTkFrame(self, width=480, height=360)
        self.cam_frame.pack(pady=10)
        self.cam_frame.pack_propagate(False)
        self.cam_label = ctk.CTkLabel(self.cam_frame, text="Démarrage caméra...")
        self.cam_label.pack(expand=True)

        # Progression (FIX 10 — basée sur 30, pas de max dur)
        self.prog_label = ctk.CTkLabel(
            self,
            text=f"{self.photo_count} / ∞  (recommandé: {OPTIMAL_PHOTOS}+)",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.prog_label.pack(pady=(5, 2))

        self.prog_bar = ctk.CTkProgressBar(self, width=400)
        self.prog_bar.pack(pady=2)
        self.prog_bar.set(min(self.photo_count / OPTIMAL_PHOTOS, 1.0))

        # Boutons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=10)

        ctk.CTkButton(
            btn_frame, text="📸 CAPTURER (Espace)",
            width=160, height=45, command=self._capture
        ).pack(side="left", padx=10)

        self.finish_btn = ctk.CTkButton(
            btn_frame, text="▶ Démarrer", width=140, height=45,
            fg_color="#27ae60",
            state="normal" if self.photo_count >= MIN_PHOTOS else "disabled",
            command=self._finish
        )
        self.finish_btn.pack(side="left", padx=10)

        # Status
        self.status_label = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=12))
        self.status_label.pack(pady=5)

        # Raccourci clavier
        self.bind("<space>", lambda e: self._capture())

    def _cam_loop(self):
        """Boucle de capture caméra (thread dédié)."""
        self.cap = cv2.VideoCapture(0)
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(0.1)
                continue

            self.current_frame = frame.copy()

            # Dessiner les rectangles de détection
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            for (t, r, b, l) in face_recognition.face_locations(rgb):
                cv2.rectangle(frame, (l, t), (r, b), (0, 255, 0), 2)

            # Afficher dans la GUI (guard : ne pas appeler après destroy)
            if self._closing:
                break
            img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).resize((480, 360))
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self.after(0, lambda i=ctk_img: (
                self.cam_label.configure(image=i, text="")
                if not self._closing else None
            ))
            time.sleep(0.03)

        if self.cap:
            self.cap.release()
            self.cap = None

    def _capture(self):
        """Capture une photo et extrait l'encoding facial."""
        if self.current_frame is None:
            return

        rgb = cv2.cvtColor(self.current_frame, cv2.COLOR_BGR2RGB)
        locs = face_recognition.face_locations(rgb)

        if not locs:
            self.status_label.configure(text="Aucun visage détecté !", text_color="#e74c3c")
            return

        encs = face_recognition.face_encodings(rgb, locs)
        if encs:
            self.encodings.append(encs[0])
            self.photo_count += 1

            # Mise à jour progression
            self.prog_bar.set(min(self.photo_count / OPTIMAL_PHOTOS, 1.0))
            self.prog_label.configure(
                text=f"{self.photo_count} / ∞  (recommandé: {OPTIMAL_PHOTOS}+)"
            )
            self.status_label.configure(
                text=f"✓ Photo {self.photo_count} capturée",
                text_color="#2ecc71"
            )
            winsound.Beep(1000, 100)

            # FIX 10 — Activer le bouton Start à partir de 15 photos
            if self.photo_count >= MIN_PHOTOS:
                self.finish_btn.configure(state="normal")

            # FIX 10 — Changer le tip dynamiquement
            tip_idx = (self.photo_count - 1) % len(CAPTURE_TIPS)
            self.tip_label.configure(text=CAPTURE_TIPS[tip_idx])

    def _finish(self):
        """Sauvegarde les encodings (format .npz multi-utilisateurs) et appelle on_success."""
        arr = np.array(self.encodings) if self.encodings else np.empty((0, 128), dtype=np.float64)

        # Charger les utilisateurs existants pour ne pas écraser les autres
        existing_users = {}
        if os.path.exists(self.encodings_path):
            try:
                from src.crypto import is_encrypted, decrypt_encodings
                raw = open(self.encodings_path, "rb").read()
                if is_encrypted(raw):
                    raw = decrypt_encodings(raw)
                existing_users = load_authorized_users_from_bytes(raw)
            except Exception:
                pass

        existing_users[self.username] = arr

        # Sérialiser en mémoire puis chiffrer si activé
        buf = io.BytesIO()
        np.savez(buf, **existing_users)
        raw_out = buf.getvalue()

        if self.encrypt_enabled:
            from src.crypto import encrypt_encodings
            raw_out = encrypt_encodings(raw_out)

        os.makedirs(os.path.dirname(os.path.abspath(self.encodings_path)), exist_ok=True)
        with open(self.encodings_path, "wb") as f:
            f.write(raw_out)

        self._closing = True
        self.running = False
        time.sleep(0.3)  # Laisser le thread caméra libérer VideoCapture
        self.destroy()
        if self.on_success:
            self.on_success()

    def _on_close(self):
        """Fermeture propre — appelle on_cancel."""
        self._closing = True
        self.running = False
        time.sleep(0.2)
        self.destroy()
        if self.on_cancel:
            self.on_cancel()
