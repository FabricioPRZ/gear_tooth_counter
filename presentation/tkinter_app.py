"""
Capa de PRESENTACIÓN.

Interfaz gráfica con Tkinter para una estación de inspección de engranes:
- Panel izquierdo: video en vivo de una cámara IP (p.ej. ESP32-CAM) con el
  contorno del engrane y los dientes detectados dibujados encima, más un
  control de intensidad de luz (otra ESP32, con PWM) para iluminar la pieza.
- Panel derecho: la lectura actual (dientes, tipo de engrane, diámetro en
  mm, corrosión aparente y calidad Aprobado/Defectuoso — automática según
  el rango esperado de dientes, aunque el usuario puede sobrescribirla), un
  mini-calibrador píxeles->mm, y un botón para guardar el registro de la
  pieza inspeccionada en el reporte Excel.

Esta capa solo dibuja, lee/escribe la config y guarda registros; el
algoritmo de conteo vive en application/tooth_counter_service.py y el
análisis adicional (tipo/medida/corrosión) en application/gear_analysis.py.
"""
import time
import tkinter as tk
from tkinter import ttk
from typing import Optional

import cv2
from PIL import Image, ImageTk

from application.gear_analysis import (
    classify_gear_type,
    detect_corrosion,
    diameter_px_to_mm,
    measure_diameter_px,
)
from application.image_processor import to_binary_mask
from application.tooth_counter_service import ToothCounterService
from domain.models import ToothCounterConfig, ToothDetectionResult
from infrastructure.calibration_repository import CalibrationRepository
from infrastructure.ip_camera_source import DEFAULT_STREAM_URL, IPCameraSource
from infrastructure.light_controller import DEFAULT_LIGHT_HOST, LightController
from infrastructure.record_repository import InspectionRecord, RecordRepository
from infrastructure.usb_camera_source import USBCameraSource

BG_APP = "#0d1117"
BG_PANEL = "#161b22"
BG_INPUT = "#0d1117"
BORDER = "#30363d"
TEXT_PRIMARY = "#e6edf3"
TEXT_SECONDARY = "#8b949e"
ACCENT_BLUE = "#2f81f7"
ACCENT_BLUE_HOVER = "#1f6feb"
ACCENT_GREEN = "#3fb950"
ACCENT_RED = "#f85149"

# Si la ESP32-CAM no entrega ni un frame en este tiempo desde que se intenta
# conectar, se cambia solo a la cámara USB (celular) como respaldo.
CAMERA_FALLBACK_TIMEOUT_S = 8.0


class TkinterApp:
    def __init__(self, config: Optional[ToothCounterConfig] = None):
        self.config = config or ToothCounterConfig()
        self.service = ToothCounterService(self.config)
        self.records = RecordRepository()
        self.calibration = CalibrationRepository()
        self.config.pixels_per_mm = self.calibration.load_pixels_per_mm()

        self.camera = None  # IPCameraSource o USBCameraSource, misma interfaz
        self.light = LightController(DEFAULT_LIGHT_HOST)
        self._camera_source_kind = "esp32"  # "esp32" o "usb"
        self._camera_connect_started_at: Optional[float] = None
        self._auto_fallback_done = False
        self._last_result: Optional[ToothDetectionResult] = None
        self._last_gear_type = "--"
        self._last_diameter_px: Optional[float] = None
        self._last_diameter_mm: Optional[float] = None
        self._last_corrosion_label = "N/D"
        self._quality_override: Optional[str] = None  # None = calidad automática
        self._photo_image = None  # referencia viva, evita que el GC borre la imagen
        self._lote_placeholder = "Ej. LOTE-014"

        self.root = tk.Tk()
        self.root.title("Conteo de Dientes de Engrane - Estación de Inspección")
        self.root.configure(bg=BG_APP)
        self.root.geometry("1280x760")
        self.root.minsize(1080, 680)

        self._build_style()
        self._build_layout()
        self._auto_connect()
        self._tick()

    # ------------------------------------------------------------------
    # Estilos
    # ------------------------------------------------------------------
    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG_PANEL)
        style.configure("App.TFrame", background=BG_APP)
        style.configure("Header.TLabel", background=BG_APP, foreground=TEXT_PRIMARY)
        style.configure("Sub.TLabel", background=BG_APP, foreground=TEXT_SECONDARY)
        style.configure("PanelSub.TLabel", background=BG_PANEL, foreground=TEXT_SECONDARY)
        style.configure("PanelTitle.TLabel", background=BG_PANEL, foreground=TEXT_PRIMARY,
                         font=("TkDefaultFont", 11, "bold"))
        style.configure("FieldLabel.TLabel", background=BG_PANEL, foreground=TEXT_SECONDARY,
                         font=("TkDefaultFont", 9))
        style.configure("TEntry", fieldbackground=BG_INPUT, foreground=TEXT_PRIMARY,
                         insertcolor=TEXT_PRIMARY, bordercolor=BORDER)
        style.configure("Light.Horizontal.TScale", background=BG_PANEL)
        style.configure("TCheckbutton", background=BG_PANEL, foreground=TEXT_PRIMARY,
                         focuscolor=BG_PANEL)
        style.map("TCheckbutton",
                  background=[("active", BG_PANEL)],
                  foreground=[("active", TEXT_PRIMARY)])

    # ------------------------------------------------------------------
    # Layout general
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        self._build_header()

        body = ttk.Frame(self.root, style="App.TFrame")
        body.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        self._build_camera_panel(body)
        self._build_reading_panel(body)

    def _build_header(self) -> None:
        header = ttk.Frame(self.root, style="App.TFrame")
        header.pack(fill="x", padx=16, pady=16)

        title_box = ttk.Frame(header, style="App.TFrame")
        title_box.pack(side="left")
        ttk.Label(title_box, text="CONTEO DE DIENTES DE ENGRANE",
                  style="Header.TLabel", font=("TkDefaultFont", 15, "bold")).pack(anchor="w")
        ttk.Label(title_box, text="VQA · ESTACIÓN DE INSPECCIÓN 01",
                  style="Sub.TLabel", font=("TkDefaultFont", 9)).pack(anchor="w")

        status_box = ttk.Frame(header, style="App.TFrame")
        status_box.pack(side="right")
        self._status_dot = tk.Canvas(status_box, width=10, height=10, bg=BG_APP,
                                      highlightthickness=0)
        self._status_dot.pack(side="left", padx=(0, 6))
        self._status_dot_id = self._status_dot.create_oval(1, 1, 9, 9, fill=ACCENT_RED, outline="")
        self._status_label = ttk.Label(status_box, text="CÁMARA DESCONECTADA",
                                        style="Sub.TLabel", font=("TkDefaultFont", 9, "bold"))
        self._status_label.pack(side="left")

    # ------------------------------------------------------------------
    # Panel de cámara (izquierda): video + control de intensidad de luz
    # ------------------------------------------------------------------
    def _build_camera_panel(self, parent: ttk.Frame) -> None:
        panel = tk.Frame(parent, bg=BG_PANEL, highlightbackground=BORDER, highlightthickness=1)
        panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        head = ttk.Frame(panel, style="TFrame")
        head.pack(fill="x", padx=14, pady=(12, 8))
        ttk.Label(head, text="CÁMARA EN TIEMPO REAL", style="PanelTitle.TLabel").pack(side="left")

        source_row = ttk.Frame(panel, style="TFrame")
        source_row.pack(fill="x", padx=14, pady=(0, 6))
        ttk.Label(source_row, text="Fuente:", style="PanelSub.TLabel").pack(side="left")
        self._source_esp32_btn = tk.Button(
            source_row, text="ESP32-CAM", command=lambda: self._select_camera_source("esp32"),
            relief="flat", padx=10, pady=3, cursor="hand2", bd=0)
        self._source_esp32_btn.pack(side="left", padx=(8, 4))
        self._source_usb_btn = tk.Button(
            source_row, text="Cámara USB (celular)", command=lambda: self._select_camera_source("usb"),
            relief="flat", padx=10, pady=3, cursor="hand2", bd=0)
        self._source_usb_btn.pack(side="left")

        conn = ttk.Frame(panel, style="TFrame")
        conn.pack(fill="x", padx=14, pady=(0, 4))
        ttk.Label(conn, text="Dirección de la cámara:", style="PanelSub.TLabel").pack(side="left")
        self._address_var = tk.StringVar(value=DEFAULT_STREAM_URL)
        address_entry = ttk.Entry(conn, textvariable=self._address_var, width=26)
        address_entry.pack(side="left", padx=(8, 8))
        address_entry.bind("<Return>", lambda _e: self._on_connect_clicked())
        self._connect_btn = tk.Button(conn, text="Desconectar", command=self._on_connect_clicked,
                                       bg=ACCENT_BLUE, fg="white", relief="flat",
                                       activebackground=ACCENT_BLUE_HOVER, activeforeground="white",
                                       padx=12, pady=2, cursor="hand2", bd=0)
        self._connect_btn.pack(side="left")

        usb_row = ttk.Frame(panel, style="TFrame")
        usb_row.pack(fill="x", padx=14, pady=(0, 8))
        ttk.Label(usb_row, text="Índice cámara USB:", style="PanelSub.TLabel").pack(side="left")
        self._usb_index_var = tk.StringVar(value="0")
        ttk.Entry(usb_row, textvariable=self._usb_index_var, width=4).pack(side="left", padx=(8, 0))
        ttk.Label(usb_row, text="(revisa con 'ls /dev/video*' cuál te asignó DroidCam/Iriun)",
                  style="PanelSub.TLabel").pack(side="left", padx=(8, 0))

        self._refresh_source_buttons()

        video_wrap = tk.Frame(panel, bg="black")
        video_wrap.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        self._video_label = tk.Label(
            video_wrap, bg="black",
            text=f"Conectando con {DEFAULT_STREAM_URL}...",
            fg=TEXT_SECONDARY, font=("TkDefaultFont", 11), justify="center",
        )
        self._video_label.pack(fill="both", expand=True)

        self._build_light_control(panel)

    def _build_light_control(self, parent: tk.Frame) -> None:
        light_box = tk.Frame(parent, bg=BG_PANEL)
        light_box.pack(fill="x", padx=14, pady=(0, 12))

        addr_row = ttk.Frame(light_box, style="TFrame")
        addr_row.pack(fill="x", pady=(0, 4))
        ttk.Label(addr_row, text="Luz (ESP32):", style="PanelSub.TLabel").pack(side="left")
        self._light_address_var = tk.StringVar(value=DEFAULT_LIGHT_HOST)
        light_entry = ttk.Entry(addr_row, textvariable=self._light_address_var, width=18)
        light_entry.pack(side="left", padx=(8, 0))
        light_entry.bind("<Return>", lambda _e: self._on_light_address_changed())
        light_entry.bind("<FocusOut>", lambda _e: self._on_light_address_changed())

        slider_row = ttk.Frame(light_box, style="TFrame")
        slider_row.pack(fill="x")
        ttk.Label(slider_row, text="Intensidad de luz:", style="PanelSub.TLabel").pack(side="left")
        self._light_value_var = tk.StringVar(value="255")
        self._light_scale = ttk.Scale(
            slider_row, from_=0, to=255, orient="horizontal",
            style="Light.Horizontal.TScale", command=self._on_light_slider_moved,
        )
        self._light_scale.set(255)
        self._light_scale.pack(side="left", fill="x", expand=True, padx=8)
        tk.Label(slider_row, textvariable=self._light_value_var, bg=BG_PANEL, fg=TEXT_PRIMARY,
                 width=3, anchor="e").pack(side="left")

    # ------------------------------------------------------------------
    # Panel de lectura actual (derecha)
    # ------------------------------------------------------------------
    def _build_reading_panel(self, parent: ttk.Frame) -> None:
        panel = tk.Frame(parent, bg=BG_PANEL, highlightbackground=BORDER, highlightthickness=1)
        panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        head = ttk.Frame(panel, style="TFrame")
        head.pack(fill="x", padx=16, pady=(14, 10))
        ttk.Label(head, text="LECTURA ACTUAL", style="PanelTitle.TLabel").pack(side="left")
        ttk.Label(head, text="PIEZA", style="PanelSub.TLabel").pack(side="right")

        grid = ttk.Frame(panel, style="TFrame")
        grid.pack(fill="x", padx=16, pady=(0, 4))
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        self._count_var = self._build_stat_box(grid, "NÚMERO DE DIENTES", "0", row=0, col=0, unit="DIENTES")
        self._gear_type_var = self._build_stat_box(grid, "TIPO DE ENGRANE", "--", row=0, col=1)
        self._diameter_var = self._build_stat_box(grid, "DIÁMETRO", "--", row=1, col=0, unit="mm")
        self._corrosion_var = self._build_stat_box(grid, "CORROSIÓN", "N/D", row=1, col=1)

        self._build_calibration_row(panel)

        ttk.Label(panel, text="CALIDAD", style="FieldLabel.TLabel").pack(anchor="w", padx=16, pady=(8, 0))
        quality_wrap = ttk.Frame(panel, style="TFrame")
        quality_wrap.pack(fill="x", padx=16, pady=(4, 14))
        quality_wrap.columnconfigure(0, weight=1)
        quality_wrap.columnconfigure(1, weight=1)
        self._approve_btn = tk.Button(
            quality_wrap, text="✓ Aprobado", command=lambda: self._set_quality_override("Aprobado"),
            relief="flat", padx=10, pady=8, cursor="hand2", bd=0)
        self._approve_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._reject_btn = tk.Button(
            quality_wrap, text="✗ Defectuoso", command=lambda: self._set_quality_override("Defectuoso"),
            relief="flat", padx=10, pady=8, cursor="hand2", bd=0)
        self._reject_btn.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self._refresh_quality_buttons()

        ttk.Label(panel, text="IDENTIFICADOR / LOTE (OPCIONAL)", style="FieldLabel.TLabel").pack(
            anchor="w", padx=16)
        self._lote_var = tk.StringVar()
        lote_entry = tk.Entry(panel, textvariable=self._lote_var, bg=BG_INPUT, fg=TEXT_SECONDARY,
                               insertbackground=TEXT_PRIMARY, relief="flat",
                               highlightbackground=BORDER, highlightthickness=1, bd=0)
        lote_entry.pack(fill="x", padx=16, pady=(4, 16), ipady=6)
        self._apply_placeholder(lote_entry, self._lote_var, self._lote_placeholder)

        add_btn = tk.Button(panel, text="Agregar registro", command=self._on_add_record,
                             bg=ACCENT_BLUE, fg="white", relief="flat",
                             activebackground=ACCENT_BLUE_HOVER, activeforeground="white",
                             font=("TkDefaultFont", 10, "bold"), pady=8, cursor="hand2", bd=0)
        add_btn.pack(fill="x", padx=16)

        self._feedback_var = tk.StringVar(value="")
        ttk.Label(panel, textvariable=self._feedback_var, style="PanelSub.TLabel",
                  wraplength=280, justify="left").pack(anchor="w", padx=16, pady=(10, 8))

        self._build_detection_panel(panel)

    # ------------------------------------------------------------------
    # Calibración de la DETECCIÓN (binarización + filtro de forma).
    #
    # La binarización depende muchísimo de la iluminación y del fondo: si el
    # engrane y el fondo tienen brillo parecido, se fusionan en una sola
    # mancha y el filtro de forma la rechaza. Estos controles permiten
    # ajustarlo en vivo, y "Ver máscara B/N" muestra exactamente lo que el
    # algoritmo ve (blanco = objeto, negro = fondo), que es la forma más
    # rápida de entender por qué no detecta.
    # ------------------------------------------------------------------
    def _build_detection_panel(self, panel: tk.Frame) -> None:
        ttk.Label(panel, text="CALIBRACIÓN DE DETECCIÓN",
                  style="PanelTitle.TLabel").pack(anchor="w", padx=16, pady=(6, 6))

        toggles = ttk.Frame(panel, style="TFrame")
        toggles.pack(fill="x", padx=16)
        toggles.columnconfigure(0, weight=1)
        toggles.columnconfigure(1, weight=1)

        self._show_mask_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(toggles, text="Ver máscara B/N",
                        variable=self._show_mask_var).grid(row=0, column=0, sticky="w")
        self._otsu_var = tk.BooleanVar(value=self.config.use_otsu)
        ttk.Checkbutton(toggles, text="Umbral automático (Otsu)",
                        variable=self._otsu_var).grid(row=0, column=1, sticky="w")
        self._invert_var = tk.BooleanVar(value=self.config.invert_binary)
        ttk.Checkbutton(toggles, text="Invertir B/N",
                        variable=self._invert_var).grid(row=1, column=0, sticky="w")
        self._shape_filter_var = tk.BooleanVar(value=self.config.use_shape_filter)
        ttk.Checkbutton(toggles, text="Filtro de forma",
                        variable=self._shape_filter_var).grid(row=1, column=1, sticky="w")

        sliders = ttk.Frame(panel, style="TFrame")
        sliders.pack(fill="x", padx=16, pady=(6, 14))
        sliders.columnconfigure(1, weight=1)

        self._threshold_var = self._build_slider(
            sliders, 0, "Umbral", 0, 255, self.config.manual_threshold)
        self._blur_var = self._build_slider(
            sliders, 1, "Desenfoque", 1, 31, self.config.blur_kernel)
        self._morph_var = self._build_slider(
            sliders, 2, "Morfología", 0, 31, self.config.morph_kernel)
        self._area_var = self._build_slider(
            sliders, 3, "Área mín. x100", 1, 500, self.config.min_contour_area // 100)

    @staticmethod
    def _build_slider(parent: ttk.Frame, row: int, label: str,
                       from_: int, to: int, initial: int) -> tk.IntVar:
        ttk.Label(parent, text=label, style="FieldLabel.TLabel").grid(
            row=row, column=0, sticky="w", pady=1)
        var = tk.IntVar(value=initial)
        value_label = ttk.Label(parent, text=str(initial), style="PanelSub.TLabel", width=4)

        def on_move(raw_value: str) -> None:
            value = int(float(raw_value))
            var.set(value)
            value_label.config(text=str(value))

        scale = ttk.Scale(parent, from_=from_, to=to, orient="horizontal", command=on_move)
        scale.set(initial)
        scale.grid(row=row, column=1, sticky="ew", padx=8, pady=1)
        value_label.grid(row=row, column=2, sticky="e")
        return var

    def _apply_detection_settings(self) -> None:
        """Vuelca los controles de calibración en la config que usa el
        servicio de conteo (que guarda una referencia viva a este objeto)."""
        cfg = self.config
        cfg.use_otsu = self._otsu_var.get()
        cfg.invert_binary = self._invert_var.get()
        cfg.use_shape_filter = self._shape_filter_var.get()
        cfg.manual_threshold = self._threshold_var.get()
        cfg.blur_kernel = max(1, self._blur_var.get())
        cfg.morph_kernel = self._morph_var.get()
        cfg.min_contour_area = max(100, self._area_var.get() * 100)

    @staticmethod
    def _build_stat_box(grid: ttk.Frame, label: str, initial: str, row: int, col: int,
                         unit: str = "") -> tk.StringVar:
        wrap = ttk.Frame(grid, style="TFrame")
        wrap.grid(row=row * 2, column=col, sticky="ew", padx=(0 if col == 0 else 6, 6 if col == 0 else 0))
        ttk.Label(wrap, text=label, style="FieldLabel.TLabel").pack(anchor="w")

        box = tk.Frame(grid, bg=BG_INPUT, highlightbackground=BORDER, highlightthickness=1)
        box.grid(row=row * 2 + 1, column=col, sticky="ew",
                 padx=(0 if col == 0 else 6, 6 if col == 0 else 0), pady=(4, 10))

        var = tk.StringVar(value=initial)
        tk.Label(box, textvariable=var, bg=BG_INPUT, fg=TEXT_PRIMARY,
                 font=("TkDefaultFont", 14, "bold"), anchor="w").pack(
            side="left", padx=10, pady=6, fill="x", expand=True)
        if unit:
            tk.Label(box, text=unit, bg=BG_INPUT, fg=TEXT_SECONDARY,
                     font=("TkDefaultFont", 8)).pack(side="right", padx=8)
        return var

    def _build_calibration_row(self, panel: tk.Frame) -> None:
        row = ttk.Frame(panel, style="TFrame")
        row.pack(fill="x", padx=16, pady=(0, 8))
        ttk.Label(row, text="Calibrar con pieza actual (mm):", style="PanelSub.TLabel").pack(side="left")
        self._calibration_var = tk.StringVar()
        calib_entry = ttk.Entry(row, textvariable=self._calibration_var, width=8)
        calib_entry.pack(side="left", padx=(8, 8))
        calib_btn = tk.Button(row, text="Calibrar", command=self._on_calibrate_clicked,
                               relief="flat", padx=8, pady=2, cursor="hand2", bd=0,
                               bg=BG_INPUT, fg=TEXT_PRIMARY, activebackground=BORDER)
        calib_btn.pack(side="left")

    @staticmethod
    def _apply_placeholder(entry: tk.Entry, var: tk.StringVar, placeholder: str) -> None:
        var.set(placeholder)

        def on_focus_in(_event):
            if var.get() == placeholder:
                var.set("")
                entry.config(fg=TEXT_PRIMARY)

        def on_focus_out(_event):
            if not var.get():
                var.set(placeholder)
                entry.config(fg=TEXT_SECONDARY)

        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)

    # ------------------------------------------------------------------
    # Conexión de cámara. open() nunca bloquea: lanza un hilo de fondo
    # que conecta y, si la señal se cae, reintenta solo cada pocos
    # segundos. Hay dos fuentes intercambiables (misma interfaz
    # open/read/is_connected/last_error/release): la ESP32-CAM por WiFi
    # y una cámara USB local (típicamente el celular con DroidCam/Iriun
    # Webcam) — si la ESP32-CAM no da imagen a tiempo, se cambia sola a
    # la USB (ver _tick). También se puede forzar con los botones "Fuente".
    # ------------------------------------------------------------------
    def _auto_connect(self) -> None:
        self._camera_source_kind = "esp32"
        self._camera_connect_started_at = time.time()
        self._auto_fallback_done = False
        self._connect_to(self._address_var.get())

    def _connect_to(self, address: str) -> None:
        url = IPCameraSource.build_stream_url(address)
        camera = IPCameraSource(url)
        camera.open()
        self.camera = camera
        self._connect_btn.config(text="Desconectar")

    def _connect_usb(self) -> None:
        try:
            index = int(self._usb_index_var.get().strip())
        except ValueError:
            index = 0
        camera = USBCameraSource(index)
        camera.open()
        self.camera = camera
        self._connect_btn.config(text="Desconectar")

    def _select_camera_source(self, kind: str) -> None:
        if self.camera is not None:
            self.camera.release()
            self.camera = None
        self._camera_source_kind = kind
        self._camera_connect_started_at = time.time()
        self._auto_fallback_done = (kind == "usb")
        self._refresh_source_buttons()
        self._video_label.config(image="", text="Conectando...")
        if kind == "esp32":
            self._connect_to(self._address_var.get())
        else:
            self._connect_usb()

    def _refresh_source_buttons(self) -> None:
        esp32_active = self._camera_source_kind == "esp32"
        self._source_esp32_btn.config(
            bg=ACCENT_BLUE if esp32_active else BG_INPUT,
            fg="white" if esp32_active else TEXT_SECONDARY,
        )
        self._source_usb_btn.config(
            bg=ACCENT_BLUE if not esp32_active else BG_INPUT,
            fg="white" if not esp32_active else TEXT_SECONDARY,
        )

    def _on_connect_clicked(self) -> None:
        if self.camera is not None:
            self.camera.release()
            self.camera = None
            self._connect_btn.config(text="Conectar")
            self._set_connection_status(False)
            self._video_label.config(
                image="", text="Desconectado.\nPulsa 'Conectar' para reintentar.")
            return

        self._camera_connect_started_at = time.time()
        if self._camera_source_kind == "esp32":
            self._auto_fallback_done = False
            self._connect_to(self._address_var.get())
        else:
            self._connect_usb()

    def _set_connection_status(self, connected: bool) -> None:
        color = ACCENT_GREEN if connected else ACCENT_RED
        text = "CÁMARA CONECTADA" if connected else "CÁMARA DESCONECTADA"
        self._status_dot.itemconfig(self._status_dot_id, fill=color)
        self._status_label.config(text=text)

    # ------------------------------------------------------------------
    # Control de luz (ESP32 aparte, vía HTTP). El envío es no bloqueante:
    # LightController lo maneja en su propio hilo de fondo.
    # ------------------------------------------------------------------
    def _on_light_address_changed(self) -> None:
        self.light.set_host(self._light_address_var.get())

    def _on_light_slider_moved(self, value: str) -> None:
        brightness = int(float(value))
        self._light_value_var.set(str(brightness))
        self.light.set_brightness(brightness)

    # ------------------------------------------------------------------
    # Calidad: automática según el rango esperado de dientes, con opción
    # de que el usuario la sobrescriba haciendo clic en un botón.
    # ------------------------------------------------------------------
    def _set_quality_override(self, quality: str) -> None:
        self._quality_override = quality
        self._refresh_quality_buttons()

    def _auto_quality(self) -> Optional[str]:
        result = self._last_result
        if result is None or not result.success:
            return None
        return "Defectuoso" if result.warning else "Aprobado"

    def _current_quality(self) -> Optional[str]:
        return self._quality_override or self._auto_quality()

    def _refresh_quality_buttons(self) -> None:
        quality = self._current_quality()
        approved = quality == "Aprobado"
        rejected = quality == "Defectuoso"
        self._approve_btn.config(
            bg=ACCENT_GREEN if approved else BG_INPUT,
            fg="white" if approved else TEXT_SECONDARY,
        )
        self._reject_btn.config(
            bg=ACCENT_RED if rejected else BG_INPUT,
            fg="white" if rejected else TEXT_SECONDARY,
        )

    # ------------------------------------------------------------------
    # Calibración píxeles -> mm: se usa el diámetro (círculo mínimo) de la
    # pieza detectada AHORA MISMO junto con su medida real conocida.
    # ------------------------------------------------------------------
    def _on_calibrate_clicked(self) -> None:
        if self._last_diameter_px is None:
            self._feedback_var.set("No hay un engrane detectado ahora mismo para calibrar.")
            return
        try:
            known_mm = float(self._calibration_var.get().strip())
            if known_mm <= 0:
                raise ValueError
        except ValueError:
            self._feedback_var.set("Escribe el diámetro real de la pieza actual en mm (ej. 42.5).")
            return

        pixels_per_mm = self._last_diameter_px / known_mm
        self.config.pixels_per_mm = pixels_per_mm
        self.calibration.save_pixels_per_mm(pixels_per_mm)
        self._feedback_var.set(
            f"Calibrado: {pixels_per_mm:.2f} px/mm (con pieza de {known_mm:.1f} mm)."
        )

    # ------------------------------------------------------------------
    # Registro (guardado en el reporte Excel vía RecordRepository)
    # ------------------------------------------------------------------
    def _on_add_record(self) -> None:
        quality = self._current_quality()
        if quality is None:
            self._feedback_var.set("Todavía no hay una lectura válida para registrar.")
            return

        lote = self._lote_var.get().strip()
        if lote == self._lote_placeholder:
            lote = ""

        tooth_count = (
            self._last_result.tooth_count
            if self._last_result and self._last_result.success else 0
        )
        record = InspectionRecord(
            tooth_count=tooth_count,
            gear_type=self._last_gear_type,
            diameter_mm=self._last_diameter_mm,
            corrosion=self._last_corrosion_label,
            quality=quality,
            lote=lote,
        )
        self.records.add(record)

        self._feedback_var.set(
            f"Registro guardado: {tooth_count} dientes, {quality}"
            + (f", lote {lote}" if lote else "")
            + f" → {self.records.xlsx_path}"
        )
        self._quality_override = None
        self._lote_var.set(self._lote_placeholder)
        self._refresh_quality_buttons()

    # ------------------------------------------------------------------
    # Bucle de video (se reprograma con root.after, sin bloquear la GUI)
    # ------------------------------------------------------------------
    def _tick(self) -> None:
        if self.camera is not None:
            connected = self.camera.is_connected()
            self._set_connection_status(connected)

            ok, frame = self.camera.read()
            if ok and frame is not None:
                self._apply_detection_settings()
                result = self.service.analyze(frame)
                self._last_result = result
                self._update_reading(frame, result)

                if self._show_mask_var.get():
                    # Vista de depuración: exactamente lo que ve el algoritmo.
                    mask = to_binary_mask(frame, self.config)
                    output = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
                else:
                    output = self._draw_result(frame, result)

                self._show_frame(output)
                self._refresh_quality_buttons()
            elif not connected:
                # Todavía no llega ningún frame (primera conexión o se cayó
                # la señal): el hilo de fondo sigue reintentando solo, así
                # que solo informamos, sin bloquear ni requerir acción.
                error = self.camera.last_error()
                self._video_label.config(
                    image="", text=error or "Buscando señal de la cámara...")

                if (
                    self._camera_source_kind == "esp32"
                    and not self._auto_fallback_done
                    and self._camera_connect_started_at is not None
                    and time.time() - self._camera_connect_started_at > CAMERA_FALLBACK_TIMEOUT_S
                ):
                    self._auto_fallback_done = True
                    self._feedback_var.set(
                        "No se detectó la ESP32-CAM a tiempo; cambiando automáticamente "
                        "a la cámara USB del celular."
                    )
                    self._select_camera_source("usb")

        self.root.after(30, self._tick)

    def _update_reading(self, frame, result: ToothDetectionResult) -> None:
        if not result.success:
            self._count_var.set("--")
            self._gear_type_var.set("--")
            self._diameter_var.set("--")
            self._corrosion_var.set("N/D")
            self._last_gear_type = "--"
            self._last_diameter_px = None
            self._last_diameter_mm = None
            self._last_corrosion_label = "N/D"
            return

        self._count_var.set(str(result.tooth_count))

        gear_type = classify_gear_type(result.tooth_count)
        self._last_gear_type = gear_type
        self._gear_type_var.set(gear_type)

        diameter_px = measure_diameter_px(result.contour)
        diameter_mm = diameter_px_to_mm(diameter_px, self.config.pixels_per_mm)
        self._last_diameter_px = diameter_px
        self._last_diameter_mm = diameter_mm
        self._diameter_var.set(f"{diameter_mm:.1f}" if diameter_mm is not None else "sin calibrar")

        corrosion = detect_corrosion(frame, result.contour)
        corrosion_label = "Sí" if corrosion.has_corrosion else "No"
        self._last_corrosion_label = corrosion_label
        self._corrosion_var.set(corrosion_label)

    def _draw_result(self, frame, result: ToothDetectionResult):
        output = frame.copy()
        if result.success:
            cv2.drawContours(output, [result.contour], -1, (0, 255, 0), 2)
            cv2.circle(output, result.centroid, 5, (255, 0, 0), -1)
            for px, py in result.peak_points:
                cv2.circle(output, (px, py), 4, (0, 0, 255), -1)
            text = f"Dientes: {result.tooth_count}"
            color = (0, 255, 0) if not result.warning else (0, 165, 255)
        else:
            text = result.message
            color = (0, 0, 255)
        cv2.putText(output, text, (15, 35), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)

        # Diagnóstico: cuando se rechaza un objeto por forma, se ve EXACTAMENTE
        # qué valor dio y contra qué rango se comparó, para poder calibrar el
        # filtro sin adivinar (ver domain/models.py: min/max_circularity, etc.)
        if not result.success and result.shape_descriptors:
            cfg = self.config
            d = result.shape_descriptors
            lines = [
                f"circularidad={d['circularity']:.2f}  "
                f"(rango valido: {cfg.min_circularity:.2f}-{cfg.max_circularity:.2f})",
                f"solidez={d['solidity']:.2f}  "
                f"(rango valido: {cfg.min_solidity:.2f}-{cfg.max_solidity:.2f})",
                f"aspecto={d['aspect_ratio']:.2f}  "
                f"(rango valido: {cfg.min_aspect_ratio:.2f}-{cfg.max_aspect_ratio:.2f})",
            ]
            y = 70
            for line in lines:
                cv2.putText(output, line, (15, y), cv2.FONT_HERSHEY_SIMPLEX,
                            0.55, (0, 200, 255), 1)
                y += 24
        return output

    def _show_frame(self, frame_bgr) -> None:
        label_w = self._video_label.winfo_width() or 640
        label_h = self._video_label.winfo_height() or 480

        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        h, w = frame_rgb.shape[:2]
        scale = min(label_w / w, label_h / h) if label_w > 1 and label_h > 1 else 1.0
        new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
        resized = cv2.resize(frame_rgb, (new_w, new_h))

        image = Image.fromarray(resized)
        self._photo_image = ImageTk.PhotoImage(image=image)
        self._video_label.config(image=self._photo_image, text="")

    # ------------------------------------------------------------------
    def run(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self) -> None:
        if self.camera is not None:
            self.camera.release()
        self.light.stop()
        self.root.destroy()
