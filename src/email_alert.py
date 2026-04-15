"""
Alertes email SMTP pour événements CRITICAL — Sprint 2 Feature 4.
Rate-limit : max 1 email toutes les 300 secondes.
Mot de passe SMTP stocké en base64 dans config.json.
Envoi dans un thread daemon pour ne pas bloquer la GUI.
"""
import base64
import smtplib
import threading
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.intrusion_report import IntrusionEvent


RATE_LIMIT_SECONDS = 300  # 5 minutes entre deux emails


class EmailAlerter:
    """Envoie des alertes email pour les intrusions CRITICAL."""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_user: str,
        smtp_password_b64: str,
        recipient: str,
    ):
        self._host = smtp_host
        self._port = smtp_port
        self._user = smtp_user
        self._password = self._decode_password(smtp_password_b64)
        self._recipient = recipient
        self._last_sent: float = 0.0
        self._lock = threading.Lock()

    def send_critical_alert(self, event: "IntrusionEvent") -> bool:
        """
        Déclenche l'envoi d'un email CRITICAL si le rate-limit le permet.
        Thread-safe. Retourne True si envoi lancé, False si ignoré ou config vide.
        """
        if not self._host or not self._recipient or not self._user:
            return False

        with self._lock:
            now = time.time()
            if now - self._last_sent < RATE_LIMIT_SECONDS:
                return False
            self._last_sent = now

        # Envoi dans un thread daemon pour ne pas bloquer la GUI
        threading.Thread(target=self._send, args=(event,), daemon=True).start()
        return True

    def _send(self, event: "IntrusionEvent") -> None:
        """Envoi effectif (exécuté dans un thread daemon)."""
        try:
            ts = datetime.fromtimestamp(event.start_time).strftime("%Y-%m-%d %H:%M:%S")
            subject = f"[PrankGuard] CRITICAL — {event.intrusion_type.value}"
            body = (
                f"Intrusion CRITICAL détectée sur PrankGuard\n\n"
                f"Type     : {event.intrusion_type.value}\n"
                f"Date     : {ts}\n"
                f"Durée    : {event.duration:.1f}s\n"
                f"Actions  : {', '.join(event.actions_taken) or 'aucune'}\n"
                f"Devices  : {', '.join(event.devices_plugged) or 'aucun'}\n"
                f"Spoof    : {'OUI' if event.spoof_detected else 'non'}\n"
            )
            msg = MIMEMultipart()
            msg["From"] = self._user
            msg["To"] = self._recipient
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            with smtplib.SMTP(self._host, self._port, timeout=10) as server:
                server.starttls()
                server.login(self._user, self._password)
                server.sendmail(self._user, self._recipient, msg.as_string())
        except Exception:
            pass  # Silencieux — ne pas crasher l'app si l'email échoue

    def reconfigure(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_user: str,
        smtp_password_b64: str,
        recipient: str,
    ) -> None:
        """Met à jour la configuration SMTP sans recréer l'objet."""
        self._host = smtp_host
        self._port = smtp_port
        self._user = smtp_user
        self._password = self._decode_password(smtp_password_b64)
        self._recipient = recipient

    @staticmethod
    def _decode_password(b64: str) -> str:
        """Décode le mot de passe base64. Retourne chaîne vide si invalide."""
        if not b64:
            return ""
        try:
            return base64.b64decode(b64.encode()).decode("utf-8")
        except Exception:
            return ""

    @staticmethod
    def encode_password(plain: str) -> str:
        """Encode un mot de passe en base64 pour le stockage dans config.json."""
        return base64.b64encode(plain.encode("utf-8")).decode("ascii")
