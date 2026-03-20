import cv2
import face_recognition
import pickle
import ctypes
import ctypes.wintypes
import time
import keyboard
import winsound
import subprocess
import customtkinter as ctk
from PIL import Image, ImageTk
from datetime import datetime
import threading
import os
import wmi
import pythoncom

ENCODINGS_PATH = 'data/owner_faces/encodings.pkl'

WM_DEVICECHANGE = 0x0219
DBT_DEVICEARRIVAL = 0x8000
DBT_DEVTYP_DEVICEINTERFACE = 5
DEVICE_NOTIFY_WINDOW_HANDLE = 0
GUID_DEVINTERFACE_USB_DEVICE = "{A5DCBF10-6530-11D2-901F-00C04FB951ED}"

def check_enrollment():
    if not os.path.exists(ENCODINGS_PATH):
        return False
    try:
        with open(ENCODINGS_PATH, 'rb') as f:
            return len(pickle.load(f)) > 0
    except:
        return False

class EnrollmentWindow(ctk.CTk):
    def __init__(self, on_complete):
        super().__init__()
        self.on_complete = on_complete
        self.title("PrankGuard - Setup")
        self.geometry("700x550")
        self.encodings = []
        self.cap = None
        self.running = True
        self.photo_count = 0
        
        if os.path.exists(ENCODINGS_PATH):
            try:
                with open(ENCODINGS_PATH, 'rb') as f:
                    self.encodings = pickle.load(f)
            except: pass
        
        ctk.CTkLabel(self, text="PrankGuard Setup", font=ctk.CTkFont(size=28, weight="bold")).pack(pady=20)
        ctk.CTkLabel(self, text="Press SPACE to capture.", font=ctk.CTkFont(size=13)).pack(pady=5)
        self.cam_frame = ctk.CTkFrame(self, width=480, height=360)
        self.cam_frame.pack(pady=15)
        self.cam_frame.pack_propagate(False)
        self.cam_label = ctk.CTkLabel(self.cam_frame, text="Starting...")
        self.cam_label.pack(expand=True)
        self.prog_label = ctk.CTkLabel(self, text="0 / 30", font=ctk.CTkFont(size=16, weight="bold"))
        self.prog_label.pack(pady=5)
        self.prog_bar = ctk.CTkProgressBar(self, width=400)
        self.prog_bar.pack(pady=5)
        self.prog_bar.set(0)
        bf = ctk.CTkFrame(self, fg_color="transparent")
        bf.pack(pady=15)
        ctk.CTkButton(bf, text="CAPTURE", width=150, height=45, command=self.capture).pack(side="left", padx=10)
        self.finish_btn = ctk.CTkButton(bf, text="Start", width=120, height=45, fg_color="#27ae60", state="disabled", command=self.finish)
        self.finish_btn.pack(side="left", padx=10)
        self.status = ctk.CTkLabel(self, text="")
        self.status.pack(pady=5)
        self.bind("<space>", lambda e: self.capture())
        threading.Thread(target=self.cam_loop, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
    
    def cam_loop(self):
        self.cap = cv2.VideoCapture(0)
        while self.running:
            ret, frame = self.cap.read()
            if not ret: time.sleep(0.1); continue
            self.current_frame = frame.copy()
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            for (t,r,b,l) in face_recognition.face_locations(rgb):
                cv2.rectangle(frame, (l,t), (r,b), (0,255,0), 2)
            img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).resize((480,360))
            imgtk = ImageTk.PhotoImage(image=img)
            self.after(0, lambda i=imgtk: (self.cam_label.configure(image=i, text=""), setattr(self.cam_label, 'image', i)))
            time.sleep(0.03)
        if self.cap: self.cap.release()
    
    def capture(self):
        if not hasattr(self, 'current_frame'): return
        rgb = cv2.cvtColor(self.current_frame, cv2.COLOR_BGR2RGB)
        locs = face_recognition.face_locations(rgb)
        if not locs: self.status.configure(text="No face!", text_color="#e74c3c"); return
        encs = face_recognition.face_encodings(rgb, locs)
        if encs:
            self.encodings.append(encs[0])
            self.photo_count += 1
            self.prog_bar.set(min(self.photo_count/30, 1.0))
            self.prog_label.configure(text=f"{self.photo_count} / 30")
            self.status.configure(text=f"OK! {len(self.encodings)}", text_color="#2ecc71")
            winsound.Beep(1000, 100)
            if self.photo_count >= 10: self.finish_btn.configure(state="normal")
    
    def finish(self):
        os.makedirs('data/owner_faces', exist_ok=True)
        with open(ENCODINGS_PATH, 'wb') as f: pickle.dump(self.encodings, f)
        self.running = False
        time.sleep(0.3)
        self.destroy()
        self.on_complete()
    
    def on_close(self):
        self.running = False
        time.sleep(0.2)
        self.destroy()

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

class DEV_BROADCAST_DEVICEINTERFACE(ctypes.Structure):
    _fields_ = [("dbcc_size", ctypes.c_ulong), ("dbcc_devicetype", ctypes.c_ulong), ("dbcc_reserved", ctypes.c_ulong), ("dbcc_classguid", GUID), ("dbcc_name", ctypes.c_wchar * 256)]

class DeviceWatcher:
    def __init__(self, callback, log_func):
        self.callback = callback
        self.log = log_func
        self.hwnd = None
        self.running = True
        self.hdev_notify = None
        self.enabled = True
        
    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
    
    def _run(self):
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p)
        class WNDCLASSEX(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", ctypes.c_void_p), ("hIcon", ctypes.c_void_p), ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p), ("lpszMenuName", ctypes.c_wchar_p), ("lpszClassName", ctypes.c_wchar_p), ("hIconSm", ctypes.c_void_p)]
        
        self.wndproc = WNDPROC(self._window_proc)
        wc = WNDCLASSEX()
        wc.cbSize = ctypes.sizeof(WNDCLASSEX)
        wc.lpfnWndProc = self.wndproc
        wc.lpszClassName = "PrankGuardUSB"
        wc.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
        ctypes.windll.user32.RegisterClassExW(ctypes.byref(wc))
        self.hwnd = ctypes.windll.user32.CreateWindowExW(0, "PrankGuardUSB", "USB", 0, 0, 0, 0, 0, 0, 0, wc.hInstance, 0)
        
        if self.hwnd:
            self._register_notification()
            msg = ctypes.wintypes.MSG()
            while self.running:
                if ctypes.windll.user32.PeekMessageW(ctypes.byref(msg), self.hwnd, 0, 0, 1):
                    ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
                    ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
                time.sleep(0.01)
    
    def _register_notification(self):
        guid_str = GUID_DEVINTERFACE_USB_DEVICE.strip("{}")
        parts = guid_str.split("-")
        guid = GUID()
        guid.Data1 = int(parts[0], 16)
        guid.Data2 = int(parts[1], 16)
        guid.Data3 = int(parts[2], 16)
        guid.Data4[0] = int(parts[3][0:2], 16)
        guid.Data4[1] = int(parts[3][2:4], 16)
        for i in range(6): guid.Data4[2 + i] = int(parts[4][i*2:i*2+2], 16)
        
        dbdi = DEV_BROADCAST_DEVICEINTERFACE()
        dbdi.dbcc_size = ctypes.sizeof(DEV_BROADCAST_DEVICEINTERFACE)
        dbdi.dbcc_devicetype = DBT_DEVTYP_DEVICEINTERFACE
        dbdi.dbcc_classguid = guid
        self.hdev_notify = ctypes.windll.user32.RegisterDeviceNotificationW(self.hwnd, ctypes.byref(dbdi), DEVICE_NOTIFY_WINDOW_HANDLE)
    
    def _window_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_DEVICECHANGE and wparam == DBT_DEVICEARRIVAL and self.enabled:
            self.callback("USB")
        return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    
    def stop(self):
        self.running = False
        if self.hdev_notify: ctypes.windll.user32.UnregisterDeviceNotification(self.hdev_notify)

class PollingWatcher:
    def __init__(self, callback, log_func):
        self.callback = callback
        self.log = log_func
        self.running = True
        self.paused = False
        self.baselines = {}
        self.wmi = None
        
        self.watch_usb_hid = True
        self.watch_monitors = False
        self.watch_network = False
        self.watch_printers = False
        self.watch_bluetooth = False
        self.watch_audio = False
    
    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
    
    def _run(self):
        pythoncom.CoInitialize()
        try:
            self.wmi = wmi.WMI()
            self._init_baselines()
            self.log("Polling Watcher: Active")
            
            while self.running:
                if not self.paused:
                    self._check_all()
                time.sleep(0.3)
        except Exception as e:
            self.log(f"Polling error: {e}")
        finally:
            pythoncom.CoUninitialize()
    
    def _init_baselines(self):
        try:
            self.baselines = {
                'usb_hid': len(self.wmi.Win32_USBControllerDevice()),
                'monitors': self._count_monitors(),
                'network': self._count_network(),
                'printers': self._count_printers(),
                'bluetooth': self._count_bluetooth(),
                'audio': self._count_audio()
            }
        except:
            self.baselines = {'usb_hid': 0, 'monitors': 0, 'network': 0, 'printers': 0, 'bluetooth': 0, 'audio': 0}
    
    def _count_monitors(self):
        try: return len(self.wmi.Win32_DesktopMonitor())
        except: return 0
    
    def _count_network(self):
        try: return len([n for n in self.wmi.Win32_NetworkAdapter() if n.NetConnectionStatus == 2])
        except: return 0
    
    def _count_printers(self):
        try: return len(self.wmi.Win32_Printer())
        except: return 0
    
    def _count_bluetooth(self):
        try: return len([p for p in self.wmi.Win32_PnPEntity() if p.Name and 'bluetooth' in p.Name.lower()])
        except: return 0
    
    def _count_audio(self):
        try: return len(self.wmi.Win32_SoundDevice())
        except: return 0
    
    def _check_all(self):
        try:
            if self.watch_usb_hid:
                count = len(self.wmi.Win32_USBControllerDevice())
                if count > self.baselines['usb_hid']:
                    self.callback("USB HID")
                self.baselines['usb_hid'] = count
            
            if self.watch_monitors:
                count = self._count_monitors()
                if count > self.baselines['monitors']:
                    self.callback("Monitor")
                self.baselines['monitors'] = count
            
            if self.watch_network:
                count = self._count_network()
                if count > self.baselines['network']:
                    self.callback("Network")
                self.baselines['network'] = count
            
            if self.watch_printers:
                count = self._count_printers()
                if count > self.baselines['printers']:
                    self.callback("Printer")
                self.baselines['printers'] = count
            
            if self.watch_bluetooth:
                count = self._count_bluetooth()
                if count > self.baselines['bluetooth']:
                    self.callback("Bluetooth")
                self.baselines['bluetooth'] = count
            
            if self.watch_audio:
                count = self._count_audio()
                if count > self.baselines['audio']:
                    self.callback("Audio")
                self.baselines['audio'] = count
        except:
            pass
    
    def reset_baselines(self):
        if self.wmi:
            self._init_baselines()
    
    def stop(self):
        self.running = False

class PrankGuardApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("PrankGuard v18")
        self.geometry("1100x800")
        
        self.USB_MODE = "DESKTOP"
        self.SEC_MODE = "PEDAGO"
        self.usb_blocked = False
        self.paused = False
        self.running = True
        self.current_status = "IDLE"
        
        self.watch_usb = ctk.BooleanVar(value=True)
        self.watch_usb_hid = ctk.BooleanVar(value=True)
        self.watch_monitors = ctk.BooleanVar(value=False)
        self.watch_network = ctk.BooleanVar(value=False)
        self.watch_printers = ctk.BooleanVar(value=False)
        self.watch_bluetooth = ctk.BooleanVar(value=False)
        self.watch_audio = ctk.BooleanVar(value=False)
        
        self.last_lock_time = 0
        self.LOCK_COOLDOWN = 8.0
        self.lock_mutex = threading.Lock()
        self.threat_start = None
        self.no_owner_start = None
        self.shoulder_surfer_grace_end = None
        self.was_shoulder_surfer = False
        self.alert_cooldown = 0
        self.camera_lost_time = None
        self.device_cooldown = 0  # GLOBAL cooldown for device detection
        
        self.THREAT_LOCK_DELAY = 2.0
        self.NO_OWNER_LOCK_DELAY = 10.0
        self.SHOULDER_GRACE_PERIOD = 5.0
        self.MIN_FACE_SIZE = 0.20
        self.CENTER_THRESHOLD = 0.35
        self.CAMERA_LOST_LOCK_DELAY = 3.0
        
        with open(ENCODINGS_PATH, 'rb') as f:
            self.owner_encodings = pickle.load(f)
        
        self.cap = None
        self.build_ui()
        
        self.usb_watcher = DeviceWatcher(self.on_device_arrival, self.log)
        self.usb_watcher.start()
        
        self.poll_watcher = PollingWatcher(self.on_device_arrival, self.log)
        self.poll_watcher.start()
        
        # Initial cooldown to let baselines stabilize
        self.device_cooldown = time.time() + 5
        self.log("Starting with 5s cooldown...")
        
        threading.Thread(target=self.camera_loop, daemon=True).start()
        threading.Thread(target=self.keyboard_listener, daemon=True).start()
        
        self.protocol("WM_DELETE_WINDOW", self.on_close)
    
    def on_toggle_change(self):
        # Set global cooldown FIRST
        self.device_cooldown = time.time() + 5
        
        # Then sync settings
        self.poll_watcher.watch_usb_hid = self.watch_usb_hid.get()
        self.poll_watcher.watch_monitors = self.watch_monitors.get()
        self.poll_watcher.watch_network = self.watch_network.get()
        self.poll_watcher.watch_printers = self.watch_printers.get()
        self.poll_watcher.watch_bluetooth = self.watch_bluetooth.get()
        self.poll_watcher.watch_audio = self.watch_audio.get()
        self.usb_watcher.enabled = self.watch_usb.get()
        
        # Reset baselines
        self.poll_watcher.reset_baselines()
        
        self.log("Settings updated (5s cooldown)")
    
    def enable_all_detection(self):
        self.device_cooldown = time.time() + 5
        self.watch_usb.set(True)
        self.watch_usb_hid.set(True)
        self.watch_monitors.set(True)
        self.watch_network.set(True)
        self.watch_printers.set(True)
        self.watch_bluetooth.set(True)
        self.watch_audio.set(True)
        self.poll_watcher.watch_usb_hid = True
        self.poll_watcher.watch_monitors = True
        self.poll_watcher.watch_network = True
        self.poll_watcher.watch_printers = True
        self.poll_watcher.watch_bluetooth = True
        self.poll_watcher.watch_audio = True
        self.usb_watcher.enabled = True
        self.poll_watcher.reset_baselines()
        self.log("ALL detection ENABLED (5s cooldown)")
    
    def disable_all_detection(self):
        self.watch_usb.set(False)
        self.watch_usb_hid.set(False)
        self.watch_monitors.set(False)
        self.watch_network.set(False)
        self.watch_printers.set(False)
        self.watch_bluetooth.set(False)
        self.watch_audio.set(False)
        self.poll_watcher.watch_usb_hid = False
        self.poll_watcher.watch_monitors = False
        self.poll_watcher.watch_network = False
        self.poll_watcher.watch_printers = False
        self.poll_watcher.watch_bluetooth = False
        self.poll_watcher.watch_audio = False
        self.usb_watcher.enabled = False
        self.log("ALL detection DISABLED")
    
    def on_device_arrival(self, device_type):
        # Check global cooldown FIRST
        if time.time() < self.device_cooldown:
            return
        
        if device_type == "USB" and not self.watch_usb.get(): return
        if device_type == "USB HID" and not self.watch_usb_hid.get(): return
        if device_type == "Monitor" and not self.watch_monitors.get(): return
        if device_type == "Network" and not self.watch_network.get(): return
        if device_type == "Printer" and not self.watch_printers.get(): return
        if device_type == "Bluetooth" and not self.watch_bluetooth.get(): return
        if device_type == "Audio" and not self.watch_audio.get(): return
        
        if self.paused: return
        if not self.can_lock(): return
        
        self.log(f">>> NEW: {device_type} <<<")
        winsound.Beep(2500, 300)
        self.device_cooldown = time.time() + 10
        self.after(0, lambda: self.do_lock(f"New {device_type}"))
    
    def build_ui(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=10, pady=10)
        
        hdr = ctk.CTkFrame(main, height=60, corner_radius=10)
        hdr.pack(fill="x", pady=(0,10))
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="PrankGuard v18", font=ctk.CTkFont(size=24, weight="bold")).pack(side="left", padx=20, pady=10)
        self.status_label = ctk.CTkLabel(hdr, text="STARTING", font=ctk.CTkFont(size=18, weight="bold"), text_color="#888")
        self.status_label.pack(side="right", padx=20, pady=10)
        
        self.tabs = ctk.CTkTabview(main, corner_radius=10)
        self.tabs.pack(fill="both", expand=True, pady=(0,10))
        tab_cam = self.tabs.add("Camera")
        tab_log = self.tabs.add("Logs")
        tab_set = self.tabs.add("Settings")
        
        cam_container = ctk.CTkFrame(tab_cam, corner_radius=10)
        cam_container.pack(fill="both", expand=True, padx=10, pady=10)
        self.cam_label = ctk.CTkLabel(cam_container, text="Init...")
        self.cam_label.pack(fill="both", expand=True)
        info = ctk.CTkFrame(tab_cam, height=70, corner_radius=10)
        info.pack(fill="x", padx=10, pady=(0,10))
        info.pack_propagate(False)
        self.face_info = ctk.CTkLabel(info, text="--", font=ctk.CTkFont(size=14))
        self.face_info.pack(side="left", padx=20, pady=10)
        self.countdown_label = ctk.CTkLabel(info, text="", font=ctk.CTkFont(size=18, weight="bold"), text_color="#e74c3c")
        self.countdown_label.pack(side="right", padx=20, pady=10)
        
        log_frame = ctk.CTkFrame(tab_log, corner_radius=10)
        log_frame.pack(fill="both", expand=True, padx=10, pady=10)
        self.log_box = ctk.CTkTextbox(log_frame, font=ctk.CTkFont(family="Consolas", size=12))
        self.log_box.pack(fill="both", expand=True, padx=10, pady=10)
        ctk.CTkButton(tab_log, text="Clear", width=100, command=lambda: self.log_box.delete("1.0", "end")).pack(pady=(0,10))
        self.log(f"v18 started - {len(self.owner_encodings)} faces")
        
        scroll = ctk.CTkScrollableFrame(tab_set, corner_radius=10)
        scroll.pack(fill="both", expand=True, padx=10, pady=10)
        
        ctk.CTkLabel(scroll, text="USB Block Mode", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20,5))
        self.usb_mode_var = ctk.StringVar(value="DESKTOP")
        ctk.CTkSegmentedButton(scroll, values=["DESKTOP", "LAPTOP"], variable=self.usb_mode_var, command=self.on_usb_mode).pack(pady=5)
        ctk.CTkLabel(scroll, text="DESKTOP: Storage only | LAPTOP: All USB", font=ctk.CTkFont(size=11), text_color="#888").pack()
        
        ctk.CTkLabel(scroll, text="Security Mode", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20,5))
        self.sec_mode_var = ctk.StringVar(value="PEDAGO")
        ctk.CTkSegmentedButton(scroll, values=["PEDAGO", "SECURE"], variable=self.sec_mode_var, command=self.on_sec_mode).pack(pady=5)
        ctk.CTkLabel(scroll, text="PEDAGO: Demo | SECURE: Auto-lock if no owner", font=ctk.CTkFont(size=11), text_color="#888").pack()
        
        ctk.CTkLabel(scroll, text="Device Detection", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20,5))
        ctk.CTkLabel(scroll, text="Lock when new device is connected", font=ctk.CTkFont(size=11), text_color="#2ecc71").pack()
        
        # Buttons Enable All / Disable All
        btn_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        btn_frame.pack(pady=10)
        ctk.CTkButton(btn_frame, text=" Enable All", width=120, fg_color="#27ae60", command=self.enable_all_detection).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text=" Disable All", width=120, fg_color="#e74c3c", command=self.disable_all_detection).pack(side="left", padx=5)
        
        det_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        det_frame.pack(fill="x", padx=20, pady=10)
        
        left_col = ctk.CTkFrame(det_frame, fg_color="transparent")
        left_col.pack(side="left", fill="both", expand=True, padx=10)
        
        ctk.CTkLabel(left_col, text="USB & Storage", font=ctk.CTkFont(size=13, weight="bold"), text_color="#3498db").pack(anchor="w", pady=(5,5))
        ctk.CTkSwitch(left_col, text=" USB Devices", variable=self.watch_usb, command=self.on_toggle_change).pack(anchor="w", pady=3)
        ctk.CTkSwitch(left_col, text=" HID (souris, clavier)", variable=self.watch_usb_hid, command=self.on_toggle_change).pack(anchor="w", pady=3)
        ctk.CTkSwitch(left_col, text=" Imprimantes", variable=self.watch_printers, command=self.on_toggle_change).pack(anchor="w", pady=3)
        
        right_col = ctk.CTkFrame(det_frame, fg_color="transparent")
        right_col.pack(side="right", fill="both", expand=True, padx=10)
        
        ctk.CTkLabel(right_col, text="Affichage & R�seau", font=ctk.CTkFont(size=13, weight="bold"), text_color="#9b59b6").pack(anchor="w", pady=(5,5))
        ctk.CTkSwitch(right_col, text=" Moniteurs", variable=self.watch_monitors, command=self.on_toggle_change).pack(anchor="w", pady=3)
        ctk.CTkSwitch(right_col, text=" R�seau", variable=self.watch_network, command=self.on_toggle_change).pack(anchor="w", pady=3)
        ctk.CTkSwitch(right_col, text=" Bluetooth", variable=self.watch_bluetooth, command=self.on_toggle_change).pack(anchor="w", pady=3)
        ctk.CTkSwitch(right_col, text=" Audio", variable=self.watch_audio, command=self.on_toggle_change).pack(anchor="w", pady=3)
        
        ctk.CTkLabel(scroll, text="Thresholds", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20,10))
        
        face_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        face_frame.pack(fill="x", padx=40, pady=5)
        self.face_size_label = ctk.CTkLabel(face_frame, text=f"Min Face Size: {int(self.MIN_FACE_SIZE*100)}%", width=150, anchor="w")
        self.face_size_label.pack(side="left")
        self.face_slider = ctk.CTkSlider(face_frame, from_=10, to=40, number_of_steps=30, command=self.on_face_size)
        self.face_slider.set(self.MIN_FACE_SIZE * 100)
        self.face_slider.pack(side="right", expand=True, fill="x", padx=(20,0))
        
        delay_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        delay_frame.pack(fill="x", padx=40, pady=5)
        self.delay_label = ctk.CTkLabel(delay_frame, text=f"Lock Delay: {self.THREAT_LOCK_DELAY}s", width=150, anchor="w")
        self.delay_label.pack(side="left")
        self.delay_slider = ctk.CTkSlider(delay_frame, from_=1, to=5, number_of_steps=8, command=self.on_delay)
        self.delay_slider.set(self.THREAT_LOCK_DELAY)
        self.delay_slider.pack(side="right", expand=True, fill="x", padx=(20,0))
        
        ctk.CTkButton(scroll, text="Re-enroll Face", fg_color="#e74c3c", command=self.reenroll).pack(pady=20)
        
        ftr = ctk.CTkFrame(main, height=50, corner_radius=10)
        ftr.pack(fill="x")
        ftr.pack_propagate(False)
        self.usb_lbl = ctk.CTkLabel(ftr, text="USB: OK", font=ctk.CTkFont(size=14), text_color="#2ecc71")
        self.usb_lbl.pack(side="left", padx=20, pady=10)
        self.cam_lbl = ctk.CTkLabel(ftr, text="CAM: OK", font=ctk.CTkFont(size=14), text_color="#2ecc71")
        self.cam_lbl.pack(side="left", padx=10, pady=10)
        self.mode_lbl = ctk.CTkLabel(ftr, text="DESKTOP | PEDAGO", font=ctk.CTkFont(size=14), text_color="#888")
        self.mode_lbl.pack(side="left", padx=20, pady=10)
        self.cd_lbl = ctk.CTkLabel(ftr, text="", font=ctk.CTkFont(size=12), text_color="#f39c12")
        self.cd_lbl.pack(side="left", padx=10, pady=10)
        ctk.CTkButton(ftr, text="LOCK", width=70, fg_color="#e74c3c", command=self.manual_lock).pack(side="right", padx=5, pady=10)
        self.pause_btn = ctk.CTkButton(ftr, text="PAUSE", width=70, fg_color="#f39c12", command=self.toggle_pause)
        self.pause_btn.pack(side="right", padx=5, pady=10)
        ctk.CTkButton(ftr, text="UNBLOCK", width=80, fg_color="#3498db", command=self.unblock_action).pack(side="right", padx=5, pady=10)
    
    def on_usb_mode(self, v):
        self.USB_MODE = v
        self.mode_lbl.configure(text=f"{self.USB_MODE} | {self.SEC_MODE}")
    
    def on_sec_mode(self, v):
        self.SEC_MODE = v
        self.mode_lbl.configure(text=f"{self.USB_MODE} | {self.SEC_MODE}")
    
    def on_face_size(self, v):
        self.MIN_FACE_SIZE = v / 100
        self.face_size_label.configure(text=f"Min Face Size: {int(v)}%")
    
    def on_delay(self, v):
        self.THREAT_LOCK_DELAY = v
        self.delay_label.configure(text=f"Lock Delay: {v:.1f}s")
    
    def block_usb(self):
        try:
            if self.USB_MODE == 'DESKTOP':
                subprocess.run(['reg', 'add', 'HKLM\\SYSTEM\\CurrentControlSet\\Services\\USBSTOR', '/v', 'Start', '/t', 'REG_DWORD', '/d', '4', '/f'], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                for s in ['USBHUB3', 'USBXHCI', 'USBSTOR']:
                    subprocess.run(['reg', 'add', f'HKLM\\SYSTEM\\CurrentControlSet\\Services\\{s}', '/v', 'Start', '/t', 'REG_DWORD', '/d', '4', '/f'], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        except: pass
    
    def unblock_usb(self):
        try:
            for s in ['USBHUB3', 'USBXHCI', 'USBSTOR']:
                subprocess.run(['reg', 'add', f'HKLM\\SYSTEM\\CurrentControlSet\\Services\\{s}', '/v', 'Start', '/t', 'REG_DWORD', '/d', '3', '/f'], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        except: pass
    
    def can_lock(self):
        return time.time() - self.last_lock_time > self.LOCK_COOLDOWN
    
    def do_lock(self, reason):
        with self.lock_mutex:
            if not self.can_lock(): return False
            self.log(f"LOCK: {reason}")
            self.block_usb()
            self.usb_blocked = True
            self.last_lock_time = time.time()
            self.threat_start = None
            self.no_owner_start = None
            self.shoulder_surfer_grace_end = None
            self.was_shoulder_surfer = False
            self.camera_lost_time = None
            self.usb_lbl.configure(text="USB: BLOCKED", text_color="#e74c3c")
            ctypes.windll.user32.LockWorkStation()
            return True
    
    def camera_loop(self):
        self.cap = cv2.VideoCapture(0)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        
        while self.running:
            # Show cooldowns
            cd_text = ""
            if not self.can_lock():
                r = self.LOCK_COOLDOWN - (time.time() - self.last_lock_time)
                cd_text = f"Lock CD: {r:.1f}s"
            elif time.time() < self.device_cooldown:
                r = self.device_cooldown - time.time()
                cd_text = f"Dev CD: {r:.1f}s"
            self.after(0, lambda t=cd_text: self.cd_lbl.configure(text=t))
            
            if self.paused: time.sleep(0.1); continue
            
            ret, frame = self.cap.read()
            
            if not ret or frame is None:
                self.after(0, lambda: self.cam_lbl.configure(text="CAM: LOST", text_color="#e74c3c"))
                if self.camera_lost_time is None:
                    self.camera_lost_time = time.time()
                    self.log("Camera lost!")
                elif time.time() - self.camera_lost_time > self.CAMERA_LOST_LOCK_DELAY and self.can_lock():
                    self.do_lock("Camera lost")
                    self.cap.release()
                    time.sleep(2)
                    self.cap = cv2.VideoCapture(0)
                time.sleep(0.1)
                continue
            
            self.camera_lost_time = None
            self.after(0, lambda: self.cam_lbl.configure(text="CAM: OK", text_color="#2ecc71"))
            
            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            locs = face_recognition.face_locations(rgb)
            
            owner = threat = passing = False
            info = "--"
            
            if locs:
                encs = face_recognition.face_encodings(rgb, locs)
                for loc, enc in zip(locs, encs):
                    t, r, b, l = loc
                    d = min(face_recognition.face_distance(self.owner_encodings, enc))
                    is_own = d <= 0.45
                    sz = (b - t) / h
                    off = abs((l + r) / 2 - w / 2) / (w / 2)
                    look = sz >= self.MIN_FACE_SIZE and off <= self.CENTER_THRESHOLD
                    
                    if is_own:
                        owner = True
                        col = (0, 255, 0)
                        lbl = "OWNER"
                        if self.usb_blocked:
                            self.log("Owner - USB unblocked")
                            self.unblock_usb()
                            self.usb_blocked = False
                            self.usb_lbl.configure(text="USB: OK", text_color="#2ecc71")
                            self.device_cooldown = time.time() + 5
                            self.poll_watcher.reset_baselines()
                    elif look:
                        threat = True
                        col = (255, 0, 0)
                        lbl = "THREAT"
                    else:
                        passing = True
                        col = (0, 165, 255)
                        lbl = "PASSING"
                    
                    cv2.rectangle(frame, (l, t), (r, b), col, 2)
                    cv2.putText(frame, f'{lbl} {d:.2f}', (l, t-10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
                    info = f"Sz:{sz:.0%} D:{d:.2f}"
            
            self.after(0, lambda i=info: self.face_info.configure(text=i))
            
            cd = ""
            if owner and threat:
                self.current_status = "SURFER"
                self.was_shoulder_surfer = True
                self.shoulder_surfer_grace_end = None
                self.threat_start = None
                self.no_owner_start = None
                if time.time() > self.alert_cooldown:
                    winsound.Beep(1500, 200)
                    self.log("SHOULDER SURFER")
                    self.alert_cooldown = time.time() + 2
            elif owner:
                self.current_status = "SECURE"
                self.was_shoulder_surfer = False
                self.shoulder_surfer_grace_end = None
                self.threat_start = None
                self.no_owner_start = None
            elif threat:
                self.no_owner_start = None
                if self.was_shoulder_surfer:
                    if not self.shoulder_surfer_grace_end:
                        self.shoulder_surfer_grace_end = time.time() + self.SHOULDER_GRACE_PERIOD
                        self.log("Grace 5s")
                    if time.time() < self.shoulder_surfer_grace_end:
                        self.current_status = "GRACE"
                        cd = f"Grace: {self.shoulder_surfer_grace_end - time.time():.1f}s"
                    else:
                        self.was_shoulder_surfer = False
                        self.shoulder_surfer_grace_end = None
                        self.threat_start = time.time()
                else:
                    self.current_status = "THREAT"
                    if not self.threat_start:
                        self.threat_start = time.time()
                        self.log("Threat!")
                    elif time.time() - self.threat_start > self.THREAT_LOCK_DELAY and self.can_lock():
                        self.do_lock("Threat")
                        time.sleep(2)
                        continue
                    else:
                        cd = f"LOCK: {self.THREAT_LOCK_DELAY - (time.time() - self.threat_start):.1f}s"
            else:
                self.threat_start = None
                self.was_shoulder_surfer = False
                self.shoulder_surfer_grace_end = None
                if self.SEC_MODE == "SECURE":
                    if not self.no_owner_start:
                        self.no_owner_start = time.time()
                    elif time.time() - self.no_owner_start > self.NO_OWNER_LOCK_DELAY and self.can_lock():
                        self.do_lock("No owner")
                        time.sleep(2)
                        continue
                    else:
                        cd = f"Lock: {self.NO_OWNER_LOCK_DELAY - (time.time() - self.no_owner_start):.1f}s"
                    self.current_status = "PASSING" if passing else "IDLE"
                else:
                    self.no_owner_start = None
                    self.current_status = "PASSING" if passing else "IDLE"
            
            cols = {"SECURE": "#2ecc71", "IDLE": "#888", "PASSING": "#f39c12", "THREAT": "#e74c3c", "GRACE": "#e67e22", "SURFER": "#9b59b6"}
            self.after(0, lambda: self.status_label.configure(text=self.current_status, text_color=cols.get(self.current_status, "#888")))
            self.after(0, lambda t=cd: self.countdown_label.configure(text=t))
            
            img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).resize((640, 480))
            imgtk = ImageTk.PhotoImage(image=img)
            self.after(0, lambda i=imgtk: (self.cam_label.configure(image=i, text=""), setattr(self.cam_label, 'image', i)))
            time.sleep(0.03)
        
        if self.cap: self.cap.release()
    
    def keyboard_listener(self):
        while self.running:
            if keyboard.is_pressed('l'): self.after(0, self.manual_lock); time.sleep(0.5)
            if keyboard.is_pressed('p'): self.after(0, self.toggle_pause); time.sleep(0.3)
            if keyboard.is_pressed('u'): self.after(0, self.unblock_action); time.sleep(0.3)
            time.sleep(0.05)
    
    def manual_lock(self): self.do_lock("Manual")
    
    def toggle_pause(self):
        self.paused = not self.paused
        self.poll_watcher.paused = self.paused
        self.pause_btn.configure(text="RESUME" if self.paused else "PAUSE", fg_color="#27ae60" if self.paused else "#f39c12")
        self.log("Paused" if self.paused else "Resumed")
    
    def unblock_action(self):
        self.unblock_usb()
        self.usb_blocked = False
        self.usb_lbl.configure(text="USB: OK", text_color="#2ecc71")
        self.device_cooldown = time.time() + 5
        self.poll_watcher.reset_baselines()
        self.log("USB unblocked (5s cooldown)")
    
    def log(self, m):
        try:
            self.log_box.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {m}\n")
            self.log_box.see("end")
        except: pass
    
    def reenroll(self):
        self.running = False
        self.usb_watcher.stop()
        self.poll_watcher.stop()
        time.sleep(0.3)
        self.destroy()
        start_app(True)
    
    def on_close(self):
        self.running = False
        self.usb_watcher.stop()
        self.poll_watcher.stop()
        self.unblock_usb()
        time.sleep(0.2)
        self.destroy()

def start_app(force=False):
    if force or not check_enrollment():
        EnrollmentWindow(lambda: PrankGuardApp().mainloop()).mainloop()
    else:
        PrankGuardApp().mainloop()

if __name__ == "__main__":
    start_app()
