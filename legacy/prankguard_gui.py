import cv2
import face_recognition
import pickle
import ctypes
import time
import keyboard
import winsound
import wmi
import subprocess
import customtkinter as ctk
from PIL import Image, ImageTk
from datetime import datetime
import threading
import os
import sys
import json
from scipy.spatial import distance as dist

# ==================== PATHS ====================
def get_data_path():
    appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
    data_dir = os.path.join(appdata, 'PrankGuard')
    os.makedirs(data_dir, exist_ok=True)
    return data_dir

def get_app_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

ENCODINGS_PATH = os.path.join(get_data_path(), 'encodings.pkl')
CONFIG_PATH = os.path.join(get_data_path(), 'config.json')
DEVCON_PATH = os.path.join(get_app_path(), 'tools', 'devcon.exe')

# ==================== CONFIG ====================
DEFAULT_CONFIG = {
    "exit_code": "1234",
    "usb_mode": "DESKTOP",
    "sec_mode": "PEDAGO",
    "monitor_usb": True,
    "monitor_disk": True,
    "monitor_network": False,
    "monitor_pnp": True,
    "min_face_size": 0.20,
    "threat_lock_delay": 2.0,
    "liveness_enabled": True,
    "stealth_mode": False
}

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r') as f:
            cfg = json.load(f)
            return {**DEFAULT_CONFIG, **cfg}
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, indent=2)

# ==================== LIVENESS DETECTION ====================
class LivenessDetector:
    """Détecte si c'est une vraie personne (pas une photo)"""
    
    def __init__(self):
        self.EYE_AR_THRESH = 0.25  # Seuil pour considérer l'oeil fermé
        self.EYE_AR_CONSEC_FRAMES = 2
        self.blink_counter = 0
        self.total_blinks = 0
        self.frame_counter = 0
        self.last_blink_time = 0
        self.is_live = False
        self.live_confidence = 0
        
        # Indices des landmarks pour les yeux (face_recognition)
        # On utilise les 6 points autour de chaque oeil
        self.history = []
        self.HISTORY_SIZE = 30  # ~1 seconde d'historique
    
    def eye_aspect_ratio(self, eye_points):
        """Calcule le ratio d'aspect de l'oeil (EAR)"""
        # Distances verticales
        A = dist.euclidean(eye_points[1], eye_points[5])
        B = dist.euclidean(eye_points[2], eye_points[4])
        # Distance horizontale
        C = dist.euclidean(eye_points[0], eye_points[3])
        ear = (A + B) / (2.0 * C)
        return ear
    
    def update(self, frame, face_landmarks):
        """Met à jour la détection de vivacité"""
        if not face_landmarks:
            return False
        
        try:
            # Récupère les points des yeux
            left_eye = face_landmarks['left_eye']
            right_eye = face_landmarks['right_eye']
            
            # Calcule EAR pour chaque oeil
            left_ear = self.eye_aspect_ratio(left_eye)
            right_ear = self.eye_aspect_ratio(right_eye)
            ear = (left_ear + right_ear) / 2.0
            
            # Historique pour détecter le mouvement
            self.history.append(ear)
            if len(self.history) > self.HISTORY_SIZE:
                self.history.pop(0)
            
            # Détection de clignement
            if ear < self.EYE_AR_THRESH:
                self.blink_counter += 1
            else:
                if self.blink_counter >= self.EYE_AR_CONSEC_FRAMES:
                    self.total_blinks += 1
                    self.last_blink_time = time.time()
                self.blink_counter = 0
            
            # Calcul de la variance (mouvement naturel)
            if len(self.history) >= 10:
                variance = max(self.history) - min(self.history)
                has_movement = variance > 0.02  # Micro-mouvements naturels
            else:
                has_movement = False
            
            # Déterminer si c'est une personne réelle
            recent_blink = (time.time() - self.last_blink_time) < 5.0
            
            # Score de confiance
            self.live_confidence = 0
            if recent_blink:
                self.live_confidence += 50
            if has_movement:
                self.live_confidence += 30
            if self.total_blinks >= 2:
                self.live_confidence += 20
            
            self.is_live = self.live_confidence >= 50
            
            return ear
            
        except Exception as e:
            return 0.0
    
    def reset(self):
        self.blink_counter = 0
        self.total_blinks = 0
        self.history = []
        self.is_live = False
        self.live_confidence = 0

# ==================== USB CONTROL (REAL) ====================
class USBController:
    """Contrôle réel des ports USB avec DevCon"""
    
    def __init__(self, devcon_path):
        self.devcon_path = devcon_path
        self.has_devcon = os.path.exists(devcon_path)
        if not self.has_devcon:
            print(f"[WARNING] DevCon not found at {devcon_path}")
            print("[WARNING] USB blocking will use registry method (less effective)")
    
    def block_usb_storage(self):
        """Bloque les périphériques de stockage USB"""
        if self.has_devcon:
            try:
                subprocess.run([self.devcon_path, 'disable', 'USBSTOR\\*'], 
                             capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                return True
            except:
                pass
        # Fallback registre
        return self._registry_block_storage(True)
    
    def unblock_usb_storage(self):
        """Débloque les périphériques de stockage USB"""
        if self.has_devcon:
            try:
                subprocess.run([self.devcon_path, 'enable', 'USBSTOR\\*'], 
                             capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                return True
            except:
                pass
        return self._registry_block_storage(False)
    
    def block_all_usb(self):
        """Bloque TOUS les ports USB (laptop mode)"""
        if self.has_devcon:
            try:
                subprocess.run([self.devcon_path, 'disable', 'USB\\*'], 
                             capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                return True
            except:
                pass
        return self._registry_block_all(True)
    
    def unblock_all_usb(self):
        """Débloque tous les ports USB"""
        if self.has_devcon:
            try:
                subprocess.run([self.devcon_path, 'enable', 'USB\\*'], 
                             capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                return True
            except:
                pass
        return self._registry_block_all(False)
    
    def _registry_block_storage(self, block):
        try:
            value = '4' if block else '3'
            subprocess.run([
                'reg', 'add',
                'HKLM\\SYSTEM\\CurrentControlSet\\Services\\USBSTOR',
                '/v', 'Start', '/t', 'REG_DWORD', '/d', value, '/f'
            ], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            return True
        except:
            return False
    
    def _registry_block_all(self, block):
        try:
            value = '4' if block else '3'
            for service in ['USBHUB3', 'USBXHCI', 'USBSTOR']:
                subprocess.run([
                    'reg', 'add',
                    f'HKLM\\SYSTEM\\CurrentControlSet\\Services\\{service}',
                    '/v', 'Start', '/t', 'REG_DWORD', '/d', value, '/f'
                ], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            return True
        except:
            return False

# ==================== MAIN APP ====================
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

class PrankGuardApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        self.title("PrankGuard v10")
        self.geometry("1100x800")
        self.minsize(900, 700)
        
        # Load config
        self.config = load_config()
        
        # State variables
        self.USB_MODE = self.config["usb_mode"]
        self.SEC_MODE = self.config["sec_mode"]
        self.EXIT_CODE = self.config["exit_code"]
        self.usb_blocked = False
        self.paused = False
        self.running = True
        self.current_status = "IDLE"
        self.camera_connected = True
        
        # Device monitoring toggles
        self.monitor_usb = ctk.BooleanVar(value=self.config["monitor_usb"])
        self.monitor_disk = ctk.BooleanVar(value=self.config["monitor_disk"])
        self.monitor_network = ctk.BooleanVar(value=self.config["monitor_network"])
        self.monitor_pnp = ctk.BooleanVar(value=self.config["monitor_pnp"])
        self.liveness_enabled = ctk.BooleanVar(value=self.config["liveness_enabled"])
        self.stealth_mode = ctk.BooleanVar(value=self.config["stealth_mode"])
        
        # Timer for "no monitoring active" in SECURE mode
        self.no_monitor_start = None
        self.NO_MONITOR_LOCK_DELAY = 10.0
        
        # GLOBAL LOCK COOLDOWN
        self.last_lock_time = 0
        self.LOCK_COOLDOWN = 8.0
        self.lock_mutex = threading.Lock()
        
        # Timers
        self.threat_start = None
        self.no_owner_start = None
        self.shoulder_surfer_grace_end = None
        self.was_shoulder_surfer = False
        self.alert_cooldown = 0
        self.device_lock_cooldown = 0
        self.camera_lost_time = None
        
        # Constants
        self.THREAT_LOCK_DELAY = self.config["threat_lock_delay"]
        self.NO_OWNER_LOCK_DELAY = 10.0
        self.SHOULDER_GRACE_PERIOD = 5.0
        self.MIN_FACE_SIZE = self.config["min_face_size"]
        self.CENTER_THRESHOLD = 0.35
        self.CAMERA_LOST_LOCK_DELAY = 3.0  # Lock après 3s sans caméra
        
        # Load face data
        if not os.path.exists(ENCODINGS_PATH):
            self.show_setup_required()
            return
        
        with open(ENCODINGS_PATH, 'rb') as f:
            self.owner_encodings = pickle.load(f)
        
        # USB Controller
        self.usb_controller = USBController(DEVCON_PATH)
        
        # Liveness detector
        self.liveness = LivenessDetector()
        
        # WMI for device monitoring
        self.wmi_conn = wmi.WMI()
        self.device_snapshot = self.get_device_snapshot()
        
        # Camera
        self.cap = None
        
        # Build UI
        self.build_ui()
        
        # Start threads
        self.camera_thread = threading.Thread(target=self.camera_loop, daemon=True)
        self.camera_thread.start()
        
        self.keyboard_thread = threading.Thread(target=self.keyboard_listener, daemon=True)
        self.keyboard_thread.start()
        
        self.device_thread = threading.Thread(target=self.device_monitor_loop, daemon=True)
        self.device_thread.start()
        
        # Override close button
        self.protocol("WM_DELETE_WINDOW", self.on_close_request)
        
        # Minimize to tray if stealth mode
        if self.stealth_mode.get():
            self.after(1000, self.minimize_to_tray)
    
    def show_setup_required(self):
        """Affiche un message si l'enrollment n'est pas fait"""
        dialog = ctk.CTkToplevel(self)
        dialog.title("Setup Required")
        dialog.geometry("400x200")
        dialog.transient(self)
        dialog.grab_set()
        
        label = ctk.CTkLabel(
            dialog,
            text=" Welcome to PrankGuard!\n\nNo face data found.\nPlease run the enrollment first.",
            font=ctk.CTkFont(size=14),
            justify="center"
        )
        label.pack(pady=30)
        
        btn = ctk.CTkButton(
            dialog,
            text="Run Enrollment",
            command=lambda: [dialog.destroy(), self.run_enrollment()]
        )
        btn.pack(pady=10)
        
        self.wait_window(dialog)
    
    def run_enrollment(self):
        """Lance le script d'enrollment"""
        subprocess.Popen([sys.executable, 'scripts/enroll_face.py'])
        self.destroy()
    
    def on_close_request(self):
        """Demande le code pour fermer l'application"""
        dialog = ctk.CTkInputDialog(
            text="Enter security code to exit:",
            title=" Security Check"
        )
        code = dialog.get_input()
        
        if code == self.EXIT_CODE:
            self.on_close()
        else:
            self.add_log(" Wrong exit code!")
            winsound.Beep(500, 300)
    
    def minimize_to_tray(self):
        """Minimise l'app dans la barre système"""
        self.withdraw()
        self.add_log(" Minimized to tray (stealth mode)")
    
    def show_from_tray(self):
        """Restaure l'app depuis la barre système"""
        self.deiconify()
        self.lift()
    
    def build_ui(self):
        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Header
        self.header_frame = ctk.CTkFrame(self.main_frame, height=60, corner_radius=10)
        self.header_frame.pack(fill="x", pady=(0, 10))
        self.header_frame.pack_propagate(False)
        
        self.title_label = ctk.CTkLabel(
            self.header_frame, 
            text=" PrankGuard v10 Secure", 
            font=ctk.CTkFont(size=24, weight="bold")
        )
        self.title_label.pack(side="left", padx=20, pady=10)
        
        self.status_label = ctk.CTkLabel(
            self.header_frame,
            text=" IDLE",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#888888"
        )
        self.status_label.pack(side="right", padx=20, pady=10)
        
        self.liveness_label = ctk.CTkLabel(
            self.header_frame,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#888888"
        )
        self.liveness_label.pack(side="right", padx=10, pady=10)
        
        # Tabs
        self.tabview = ctk.CTkTabview(self.main_frame, corner_radius=10)
        self.tabview.pack(fill="both", expand=True, pady=(0, 10))
        
        self.tab_camera = self.tabview.add(" Camera")
        self.tab_logs = self.tabview.add(" Logs")
        self.tab_settings = self.tabview.add(" Settings")
        self.tab_security = self.tabview.add(" Security")
        
        self.build_camera_tab()
        self.build_logs_tab()
        self.build_settings_tab()
        self.build_security_tab()
        
        # Footer
        self.footer_frame = ctk.CTkFrame(self.main_frame, height=50, corner_radius=10)
        self.footer_frame.pack(fill="x")
        self.footer_frame.pack_propagate(False)
        
        self.usb_status_label = ctk.CTkLabel(
            self.footer_frame,
            text="USB:  OK",
            font=ctk.CTkFont(size=14)
        )
        self.usb_status_label.pack(side="left", padx=20, pady=10)
        
        self.camera_status_label = ctk.CTkLabel(
            self.footer_frame,
            text=" Camera: OK",
            font=ctk.CTkFont(size=14),
            text_color="#2ecc71"
        )
        self.camera_status_label.pack(side="left", padx=10, pady=10)
        
        self.mode_label = ctk.CTkLabel(
            self.footer_frame,
            text=f"{self.USB_MODE} | {self.SEC_MODE}",
            font=ctk.CTkFont(size=14),
            text_color="#888888"
        )
        self.mode_label.pack(side="left", padx=20, pady=10)
        
        self.cooldown_label = ctk.CTkLabel(
            self.footer_frame,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#f39c12"
        )
        self.cooldown_label.pack(side="left", padx=10, pady=10)
        
        self.lock_btn = ctk.CTkButton(
            self.footer_frame,
            text=" Lock Now",
            width=100,
            fg_color="#e74c3c",
            hover_color="#c0392b",
            command=self.manual_lock
        )
        self.lock_btn.pack(side="right", padx=5, pady=10)
        
        self.pause_btn = ctk.CTkButton(
            self.footer_frame,
            text=" Pause",
            width=100,
            fg_color="#f39c12",
            hover_color="#d68910",
            command=self.toggle_pause
        )
        self.pause_btn.pack(side="right", padx=5, pady=10)
        
        self.usb_btn = ctk.CTkButton(
            self.footer_frame,
            text=" Unblock USB",
            width=120,
            fg_color="#3498db",
            hover_color="#2980b9",
            command=self.unblock_usb_action
        )
        self.usb_btn.pack(side="right", padx=5, pady=10)
    
    def build_camera_tab(self):
        self.camera_container = ctk.CTkFrame(self.tab_camera, corner_radius=10)
        self.camera_container.pack(fill="both", expand=True, padx=10, pady=10)
        
        self.camera_label = ctk.CTkLabel(
            self.camera_container, 
            text="Initializing camera...",
            font=ctk.CTkFont(size=16)
        )
        self.camera_label.pack(fill="both", expand=True)
        
        self.info_frame = ctk.CTkFrame(self.tab_camera, height=100, corner_radius=10)
        self.info_frame.pack(fill="x", padx=10, pady=(0, 10))
        self.info_frame.pack_propagate(False)
        
        self.face_info_label = ctk.CTkLabel(
            self.info_frame,
            text="No face detected",
            font=ctk.CTkFont(size=14)
        )
        self.face_info_label.pack(side="left", padx=20, pady=10)
        
        self.liveness_info_label = ctk.CTkLabel(
            self.info_frame,
            text="Liveness: --",
            font=ctk.CTkFont(size=14),
            text_color="#888888"
        )
        self.liveness_info_label.pack(side="left", padx=20, pady=10)
        
        self.countdown_label = ctk.CTkLabel(
            self.info_frame,
            text="",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#e74c3c"
        )
        self.countdown_label.pack(side="right", padx=20, pady=10)
    
    def build_logs_tab(self):
        self.logs_frame = ctk.CTkFrame(self.tab_logs, corner_radius=10)
        self.logs_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        self.log_textbox = ctk.CTkTextbox(
            self.logs_frame,
            font=ctk.CTkFont(family="Consolas", size=12),
            corner_radius=10
        )
        self.log_textbox.pack(fill="both", expand=True, padx=10, pady=10)
        
        self.clear_btn = ctk.CTkButton(
            self.tab_logs,
            text=" Clear Logs",
            width=120,
            command=self.clear_logs
        )
        self.clear_btn.pack(pady=(0, 10))
        
        self.add_log("PrankGuard v10 Secure Edition started")
        self.add_log(f"DevCon available: {self.usb_controller.has_devcon}")
        self.add_log(f"Liveness detection: {'ON' if self.liveness_enabled.get() else 'OFF'}")
    
    def build_settings_tab(self):
        self.settings_scroll = ctk.CTkScrollableFrame(self.tab_settings, corner_radius=10)
        self.settings_scroll.pack(fill="both", expand=True, padx=10, pady=10)
        
        # USB Mode
        ctk.CTkLabel(self.settings_scroll, text=" USB Block Mode",
                    font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 5))
        
        self.usb_mode_var = ctk.StringVar(value=self.USB_MODE)
        ctk.CTkSegmentedButton(
            self.settings_scroll,
            values=["DESKTOP", "LAPTOP"],
            variable=self.usb_mode_var,
            command=self.on_usb_mode_change
        ).pack(pady=5)
        
        ctk.CTkLabel(self.settings_scroll,
                    text="DESKTOP: Block storage only | LAPTOP: Block all USB",
                    font=ctk.CTkFont(size=12), text_color="#888888").pack(pady=(0, 20))
        
        # Security Mode
        ctk.CTkLabel(self.settings_scroll, text=" Security Mode",
                    font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 5))
        
        self.sec_mode_var = ctk.StringVar(value=self.SEC_MODE)
        ctk.CTkSegmentedButton(
            self.settings_scroll,
            values=["PEDAGO", "SECURE"],
            variable=self.sec_mode_var,
            command=self.on_sec_mode_change
        ).pack(pady=5)
        
        ctk.CTkLabel(self.settings_scroll,
                    text="PEDAGO: Demo mode | SECURE: Full protection",
                    font=ctk.CTkFont(size=12), text_color="#888888").pack(pady=(0, 20))
        
        # Device Monitoring
        ctk.CTkLabel(self.settings_scroll, text=" Device Monitoring",
                    font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 10))
        
        self.build_toggle(self.settings_scroll, " USB Controllers", self.monitor_usb)
        self.build_toggle(self.settings_scroll, " Disks / Storage", self.monitor_disk)
        self.build_toggle(self.settings_scroll, " Network", self.monitor_network)
        self.build_toggle(self.settings_scroll, " PnP Devices", self.monitor_pnp)
        
        # Thresholds
        ctk.CTkLabel(self.settings_scroll, text=" Detection Thresholds",
                    font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(30, 10))
        
        self.face_size_frame = ctk.CTkFrame(self.settings_scroll, fg_color="transparent")
        self.face_size_frame.pack(fill="x", padx=40, pady=5)
        
        self.face_size_label = ctk.CTkLabel(
            self.face_size_frame,
            text=f"Min Face Size: {int(self.MIN_FACE_SIZE*100)}%",
            font=ctk.CTkFont(size=14)
        )
        self.face_size_label.pack(side="left")
        
        self.face_size_slider = ctk.CTkSlider(
            self.face_size_frame, from_=10, to=40, number_of_steps=30,
            command=self.on_face_size_change
        )
        self.face_size_slider.set(self.MIN_FACE_SIZE * 100)
        self.face_size_slider.pack(side="right", expand=True, fill="x", padx=20)
        
        self.lock_delay_frame = ctk.CTkFrame(self.settings_scroll, fg_color="transparent")
        self.lock_delay_frame.pack(fill="x", padx=40, pady=5)
        
        self.lock_delay_label = ctk.CTkLabel(
            self.lock_delay_frame,
            text=f"Threat Lock Delay: {self.THREAT_LOCK_DELAY}s",
            font=ctk.CTkFont(size=14)
        )
        self.lock_delay_label.pack(side="left")
        
        self.lock_delay_slider = ctk.CTkSlider(
            self.lock_delay_frame, from_=1, to=5, number_of_steps=8,
            command=self.on_lock_delay_change
        )
        self.lock_delay_slider.set(self.THREAT_LOCK_DELAY)
        self.lock_delay_slider.pack(side="right", expand=True, fill="x", padx=20)
    
    def build_security_tab(self):
        """Onglet sécurité avancée"""
        self.security_scroll = ctk.CTkScrollableFrame(self.tab_security, corner_radius=10)
        self.security_scroll.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Exit Code
        ctk.CTkLabel(self.security_scroll, text=" Exit Code",
                    font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 10))
        
        ctk.CTkLabel(self.security_scroll,
                    text="Code required to close the application",
                    font=ctk.CTkFont(size=12), text_color="#888888").pack()
        
        self.exit_code_frame = ctk.CTkFrame(self.security_scroll, fg_color="transparent")
        self.exit_code_frame.pack(pady=10)
        
        self.exit_code_entry = ctk.CTkEntry(
            self.exit_code_frame, width=150, show="*",
            placeholder_text="Current code"
        )
        self.exit_code_entry.pack(side="left", padx=5)
        self.exit_code_entry.insert(0, self.EXIT_CODE)
        
        ctk.CTkButton(
            self.exit_code_frame, text="Update", width=80,
            command=self.update_exit_code
        ).pack(side="left", padx=5)
        
        # Liveness Detection
        ctk.CTkLabel(self.security_scroll, text=" Anti-Spoofing",
                    font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(30, 10))
        
        self.liveness_toggle = ctk.CTkSwitch(
            self.security_scroll,
            text="Liveness Detection (detects photos)",
            variable=self.liveness_enabled,
            command=self.on_liveness_change
        )
        self.liveness_toggle.pack(pady=5)
        
        ctk.CTkLabel(self.security_scroll,
                    text="Requires blinking to verify real person\nPhotos of owner will not unlock!",
                    font=ctk.CTkFont(size=12), text_color="#888888",
                    justify="center").pack()
        
        # Stealth Mode
        ctk.CTkLabel(self.security_scroll, text=" Stealth Mode",
                    font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(30, 10))
        
        self.stealth_toggle = ctk.CTkSwitch(
            self.security_scroll,
            text="Minimize to system tray on start",
            variable=self.stealth_mode,
            command=self.on_stealth_change
        )
        self.stealth_toggle.pack(pady=5)
        
        ctk.CTkButton(
            self.security_scroll,
            text=" Minimize Now",
            command=self.minimize_to_tray
        ).pack(pady=10)
        
        # Camera Protection
        ctk.CTkLabel(self.security_scroll, text=" Camera Protection",
                    font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(30, 10))
        
        ctk.CTkLabel(self.security_scroll,
                    text=" Auto-lock if camera disconnected (3 seconds)\n Cannot be bypassed by unplugging webcam",
                    font=ctk.CTkFont(size=12), text_color="#2ecc71",
                    justify="center").pack()
        
        # DevCon Status
        ctk.CTkLabel(self.security_scroll, text=" USB Blocking",
                    font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(30, 10))
        
        devcon_status = " DevCon active (instant USB blocking)" if self.usb_controller.has_devcon else " DevCon missing (registry-only blocking)"
        devcon_color = "#2ecc71" if self.usb_controller.has_devcon else "#f39c12"
        
        ctk.CTkLabel(self.security_scroll,
                    text=devcon_status,
                    font=ctk.CTkFont(size=12), text_color=devcon_color).pack()
        
        if not self.usb_controller.has_devcon:
            ctk.CTkLabel(self.security_scroll,
                        text="Download DevCon for instant USB control",
                        font=ctk.CTkFont(size=11), text_color="#888888").pack()
    
    def build_toggle(self, parent, text, variable):
        """Crée un toggle switch"""
        frame = ctk.CTkFrame(parent, corner_radius=10)
        frame.pack(fill="x", padx=20, pady=3)
        
        ctk.CTkSwitch(
            frame, text=text, variable=variable,
            command=self.on_monitor_change
        ).pack(side="left", padx=15, pady=8)
    
    def is_any_monitor_active(self):
        return (self.monitor_usb.get() or self.monitor_disk.get() or 
                self.monitor_network.get() or self.monitor_pnp.get())

    # ========== USB FUNCTIONS ==========
    def block_usb(self):
        if self.USB_MODE == 'DESKTOP':
            return self.usb_controller.block_usb_storage()
        else:
            return self.usb_controller.block_all_usb()

    def unblock_usb(self):
        if self.USB_MODE == 'DESKTOP':
            return self.usb_controller.unblock_usb_storage()
        else:
            return self.usb_controller.unblock_all_usb()
    
    def can_lock(self):
        return time.time() - self.last_lock_time > self.LOCK_COOLDOWN
    
    def do_lock(self, reason="Unknown"):
        with self.lock_mutex:
            if not self.can_lock():
                remaining = self.LOCK_COOLDOWN - (time.time() - self.last_lock_time)
                self.add_log(f" Lock blocked (cooldown: {remaining:.1f}s)")
                return False
            
            self.add_log(f" LOCKING: {reason}")
            self.block_usb()
            self.usb_blocked = True
            self.last_lock_time = time.time()
            
            # Reset ALL timers
            self.threat_start = None
            self.no_owner_start = None
            self.no_monitor_start = None
            self.shoulder_surfer_grace_end = None
            self.was_shoulder_surfer = False
            self.camera_lost_time = None
            self.liveness.reset()
            
            self.update_usb_status()
            ctypes.windll.user32.LockWorkStation()
            
            time.sleep(1)
            self.device_snapshot = self.get_device_snapshot()
            
            return True
    
    # ========== DEVICE MONITORING ==========
    def get_device_snapshot(self):
        try:
            devices = {}
            if self.monitor_usb.get():
                devices['usb'] = len(self.wmi_conn.Win32_USBControllerDevice())
            if self.monitor_disk.get():
                devices['disk'] = len(self.wmi_conn.Win32_DiskDrive())
            if self.monitor_network.get():
                devices['network'] = len([n for n in self.wmi_conn.Win32_NetworkAdapter() if n.NetConnectionStatus == 2])
            if self.monitor_pnp.get():
                devices['pnp'] = len([p for p in self.wmi_conn.Win32_PnPEntity() if 'USB' in (p.DeviceID or '')])
            return devices
        except:
            return {}
    
    def device_changed(self, old, new):
        for key in new:
            if key in old and new[key] > old.get(key, 0):
                return key, old.get(key, 0), new[key]
        return None
    
    def device_monitor_loop(self):
        while self.running:
            if not self.paused:
                # Check if no monitoring is active in SECURE mode
                if self.SEC_MODE == "SECURE" and not self.is_any_monitor_active():
                    if self.no_monitor_start is None:
                        self.no_monitor_start = time.time()
                        self.add_log(" No monitoring active in SECURE mode!")
                    elif time.time() - self.no_monitor_start > self.NO_MONITOR_LOCK_DELAY:
                        if self.can_lock():
                            self.do_lock("No device monitoring in SECURE mode")
                else:
                    self.no_monitor_start = None
                
                # Normal device monitoring
                if self.is_any_monitor_active() and self.can_lock():
                    try:
                        new_snapshot = self.get_device_snapshot()
                        change = self.device_changed(self.device_snapshot, new_snapshot)
                        if change:
                            device_type, old_count, new_count = change
                            self.add_log(f" NEW DEVICE: {device_type} ({old_count}  {new_count})")
                            winsound.Beep(2500, 500)
                            self.do_lock(f"New device: {device_type}")
                        self.device_snapshot = new_snapshot
                    except:
                        pass
            time.sleep(0.5)
    
    # ========== CAMERA LOOP ==========
    def camera_loop(self):
        self.cap = cv2.VideoCapture(0)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        
        while self.running:
            # Update cooldown display
            if not self.can_lock():
                remaining = self.LOCK_COOLDOWN - (time.time() - self.last_lock_time)
                self.after(0, lambda r=remaining: self.cooldown_label.configure(
                    text=f" Cooldown: {r:.1f}s"
                ))
            else:
                self.after(0, lambda: self.cooldown_label.configure(text=""))
            
            if self.paused:
                time.sleep(0.1)
                continue
            
            ret, frame = self.cap.read()
            
            # ===== CAMERA PROTECTION =====
            if not ret or frame is None:
                self.camera_connected = False
                self.after(0, lambda: self.camera_status_label.configure(
                    text=" Camera: LOST!", text_color="#e74c3c"
                ))
                
                if self.camera_lost_time is None:
                    self.camera_lost_time = time.time()
                    self.add_log(" Camera disconnected!")
                elif time.time() - self.camera_lost_time > self.CAMERA_LOST_LOCK_DELAY:
                    if self.can_lock():
                        self.do_lock("Camera disconnected")
                        # Try to reconnect
                        self.cap.release()
                        time.sleep(2)
                        self.cap = cv2.VideoCapture(0)
                
                time.sleep(0.1)
                continue
            else:
                self.camera_connected = True
                self.camera_lost_time = None
                self.after(0, lambda: self.camera_status_label.configure(
                    text=" Camera: OK", text_color="#2ecc71"
                ))
            
            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            locs = face_recognition.face_locations(rgb)
            
            owner_present = False
            threat_present = False
            passing_present = False
            face_info = "No face detected"
            liveness_info = "Liveness: --"
            
            if locs:
                encs = face_recognition.face_encodings(rgb, locs)
                landmarks_list = face_recognition.face_landmarks(rgb, locs)
                
                for idx, (loc, enc) in enumerate(zip(locs, encs)):
                    top, right, bottom, left = loc
                    
                    dist_score = min(face_recognition.face_distance(self.owner_encodings, enc))
                    is_owner_face = dist_score <= 0.6
                    
                    face_height = bottom - top
                    face_size_ratio = face_height / h
                    face_center_x = (left + right) / 2
                    offset_x = abs(face_center_x - w/2) / (w/2)
                    
                    is_close = face_size_ratio >= self.MIN_FACE_SIZE
                    is_centered = offset_x <= self.CENTER_THRESHOLD
                    is_looking = is_close and is_centered
                    
                    # Liveness check for owner
                    liveness_ok = True
                    if is_owner_face and self.liveness_enabled.get():
                        if idx < len(landmarks_list):
                            ear = self.liveness.update(frame, landmarks_list[idx])
                            liveness_ok = self.liveness.is_live
                            liveness_info = f"Liveness: {self.liveness.live_confidence}% | Blinks: {self.liveness.total_blinks}"
                        else:
                            liveness_ok = False
                    
                    if is_owner_face:
                        if liveness_ok or not self.liveness_enabled.get():
                            owner_present = True
                            color = (0, 255, 0)
                            label = "OWNER "
                            if self.usb_blocked:
                                self.add_log(" Owner verified - USB UNBLOCKED")
                                self.unblock_usb()
                                self.usb_blocked = False
                                self.update_usb_status()
                        else:
                            color = (255, 165, 0)  # Orange - owner mais pas vérifié
                            label = "OWNER? (blink)"
                    elif is_looking:
                        threat_present = True
                        color = (255, 0, 0)
                        label = "THREAT"
                    else:
                        passing_present = True
                        color = (0, 165, 255)
                        label = "PASSING"
                    
                    cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
                    cv2.putText(frame, f'{label} {dist_score:.2f}', (left, top-10), 
                               cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
                    
                    face_info = f"Size: {face_size_ratio:.0%} | Center: {offset_x:.0%}"
            else:
                self.liveness.reset()
            
            self.after(0, lambda info=face_info: self.face_info_label.configure(text=info))
            self.after(0, lambda info=liveness_info: self.liveness_info_label.configure(text=info))
            
            # State machine (same as before)
            countdown_text = ""
            
            if owner_present and threat_present:
                self.current_status = "SHOULDER SURFER"
                self.was_shoulder_surfer = True
                self.shoulder_surfer_grace_end = None
                self.threat_start = None
                self.no_owner_start = None
                
                if time.time() > self.alert_cooldown:
                    winsound.Beep(1500, 200)
                    self.add_log(" SHOULDER SURFER DETECTED")
                    self.alert_cooldown = time.time() + 2
            
            elif owner_present:
                self.current_status = "SECURE"
                self.was_shoulder_surfer = False
                self.shoulder_surfer_grace_end = None
                self.threat_start = None
                self.no_owner_start = None
            
            elif threat_present:
                self.no_owner_start = None
                
                if self.was_shoulder_surfer:
                    if self.shoulder_surfer_grace_end is None:
                        self.shoulder_surfer_grace_end = time.time() + self.SHOULDER_GRACE_PERIOD
                        self.add_log(" Owner left - 5s grace period")
                    
                    if time.time() < self.shoulder_surfer_grace_end:
                        remaining = self.shoulder_surfer_grace_end - time.time()
                        self.current_status = "GRACE"
                        countdown_text = f"Grace: {remaining:.1f}s"
                    else:
                        self.was_shoulder_surfer = False
                        self.shoulder_surfer_grace_end = None
                        if self.threat_start is None:
                            self.threat_start = time.time()
                else:
                    self.current_status = "THREAT"
                    if self.threat_start is None:
                        self.threat_start = time.time()
                        self.add_log(" Threat detected")
                    elif time.time() - self.threat_start > self.THREAT_LOCK_DELAY:
                        if self.can_lock():
                            self.do_lock("Threat detected")
                            time.sleep(2)
                            continue
                        else:
                            self.threat_start = None
                    else:
                        remaining = self.THREAT_LOCK_DELAY - (time.time() - self.threat_start)
                        countdown_text = f" LOCK IN: {remaining:.1f}s"
            
            else:
                self.threat_start = None
                self.was_shoulder_surfer = False
                self.shoulder_surfer_grace_end = None
                
                if self.SEC_MODE == "SECURE":
                    if self.no_owner_start is None:
                        self.no_owner_start = time.time()
                    elif time.time() - self.no_owner_start > self.NO_OWNER_LOCK_DELAY:
                        if self.can_lock():
                            self.add_log(" No owner too long")
                            self.do_lock("No owner detected")
                            time.sleep(2)
                            continue
                        else:
                            self.no_owner_start = None
                    else:
                        remaining = self.NO_OWNER_LOCK_DELAY - (time.time() - self.no_owner_start)
                        countdown_text = f"Lock in: {remaining:.1f}s"
                    self.current_status = "PASSING" if passing_present else "IDLE"
                else:
                    self.no_owner_start = None
                    self.current_status = "PASSING" if passing_present else "IDLE"
            
            self.after(0, lambda: self.update_status_display())
            self.after(0, lambda txt=countdown_text: self.countdown_label.configure(text=txt))
            
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame_rgb)
            img = img.resize((640, 480))
            imgtk = ImageTk.PhotoImage(image=img)
            
            self.after(0, lambda img=imgtk: self.update_camera_display(img))
            
            time.sleep(0.03)
        
        if self.cap:
            self.cap.release()
    
    def update_camera_display(self, imgtk):
        self.camera_label.configure(image=imgtk, text="")
        self.camera_label.image = imgtk
    
    def update_status_display(self):
        status_colors = {
            "SECURE": ("#2ecc71", " SECURE"),
            "IDLE": ("#888888", " IDLE"),
            "PASSING": ("#f39c12", " PASSING"),
            "THREAT": ("#e74c3c", " THREAT"),
            "GRACE": ("#e67e22", " GRACE"),
            "SHOULDER SURFER": ("#9b59b6", " SHOULDER SURFER")
        }
        color, text = status_colors.get(self.current_status, ("#888888", " UNKNOWN"))
        self.status_label.configure(text=text, text_color=color)
    
    def update_usb_status(self):
        if self.usb_blocked:
            self.usb_status_label.configure(text="USB:  BLOCKED", text_color="#e74c3c")
        else:
            self.usb_status_label.configure(text="USB:  OK", text_color="#2ecc71")
    
    def keyboard_listener(self):
        while self.running:
            if keyboard.is_pressed('l'):
                self.after(0, self.manual_lock)
                time.sleep(0.5)
            if keyboard.is_pressed('p'):
                self.after(0, self.toggle_pause)
                time.sleep(0.3)
            if keyboard.is_pressed('u'):
                self.after(0, self.unblock_usb_action)
                time.sleep(0.3)
            time.sleep(0.05)
    
    def manual_lock(self):
        self.do_lock("Manual lock")
    
    def toggle_pause(self):
        self.paused = not self.paused
        if self.paused:
            self.pause_btn.configure(text=" Resume", fg_color="#27ae60", hover_color="#1e8449")
            self.add_log(" Paused")
        else:
            self.pause_btn.configure(text=" Pause", fg_color="#f39c12", hover_color="#d68910")
            self.add_log(" Resumed")
    
    def unblock_usb_action(self):
        self.unblock_usb()
        self.usb_blocked = False
        self.update_usb_status()
        self.add_log(" USB manually unblocked")
    
    def add_log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}\n"
        try:
            self.log_textbox.insert("end", log_entry)
            self.log_textbox.see("end")
        except:
            pass
    
    def clear_logs(self):
        self.log_textbox.delete("1.0", "end")
        self.add_log("Logs cleared")
    
    # Settings callbacks
    def on_usb_mode_change(self, value):
        self.USB_MODE = value
        self.config["usb_mode"] = value
        save_config(self.config)
        self.mode_label.configure(text=f"{self.USB_MODE} | {self.SEC_MODE}")
        self.add_log(f" USB Mode: {value}")
    
    def on_sec_mode_change(self, value):
        self.SEC_MODE = value
        self.config["sec_mode"] = value
        save_config(self.config)
        self.mode_label.configure(text=f"{self.USB_MODE} | {self.SEC_MODE}")
        self.add_log(f" Security Mode: {value}")
    
    def on_monitor_change(self):
        self.config["monitor_usb"] = self.monitor_usb.get()
        self.config["monitor_disk"] = self.monitor_disk.get()
        self.config["monitor_network"] = self.monitor_network.get()
        self.config["monitor_pnp"] = self.monitor_pnp.get()
        save_config(self.config)
        self.device_snapshot = self.get_device_snapshot()
        self.no_monitor_start = None
        self.add_log(" Monitoring settings updated")
    
    def on_face_size_change(self, value):
        self.MIN_FACE_SIZE = value / 100
        self.config["min_face_size"] = self.MIN_FACE_SIZE
        save_config(self.config)
        self.face_size_label.configure(text=f"Min Face Size: {int(value)}%")
    
    def on_lock_delay_change(self, value):
        self.THREAT_LOCK_DELAY = value
        self.config["threat_lock_delay"] = value
        save_config(self.config)
        self.lock_delay_label.configure(text=f"Threat Lock Delay: {value:.1f}s")
    
    def on_liveness_change(self):
        self.config["liveness_enabled"] = self.liveness_enabled.get()
        save_config(self.config)
        status = "ON" if self.liveness_enabled.get() else "OFF"
        self.add_log(f" Liveness detection: {status}")
    
    def on_stealth_change(self):
        self.config["stealth_mode"] = self.stealth_mode.get()
        save_config(self.config)
        self.add_log(f" Stealth mode: {'ON' if self.stealth_mode.get() else 'OFF'}")
    
    def update_exit_code(self):
        new_code = self.exit_code_entry.get()
        if len(new_code) >= 4:
            self.EXIT_CODE = new_code
            self.config["exit_code"] = new_code
            save_config(self.config)
            self.add_log(" Exit code updated")
            winsound.Beep(1000, 200)
        else:
            self.add_log(" Code must be at least 4 characters")
    
    def on_close(self):
        self.running = False
        self.add_log("Shutting down...")
        self.unblock_usb()
        time.sleep(0.5)
        self.destroy()

if __name__ == "__main__":
    app = PrankGuardApp()
    app.mainloop()
