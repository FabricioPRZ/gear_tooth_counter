"""
Capa de PRESENTACIÓN.

Muestra el video en tiempo real con el conteo de dientes superpuesto y
expone trackbars para calibrar el algoritmo en vivo (muy útil porque la
binarización depende de tu iluminación y fondo). Esta capa solo dibuja y
lee la config; NO contiene lógica de visión por computadora.

MEJORA DE UX:
OpenCV no soporta tooltips nativos en los trackbars, así que en vez de
adivinar qué hace cada deslizador, se abre una tercera ventana
("Guia de Calibracion") con una explicación de cada control, su rango
recomendado y un pequeño diagnóstico en vivo (forma detectada, motivo de
rechazo, etc). Así el usuario ve, en el mismo momento en que mueve un
slider, qué está intentando ajustar y por qué el resultado cambia.
"""
import time

import cv2
import numpy as np

from infrastructure.light_controller import LightController
from domain.models import ToothCounterConfig, ToothDetectionResult
from application.tooth_counter_service import ToothCounterService
from infrastructure.camera_source import CameraSource

WINDOW_MAIN = "Control de Calidad - Conteo de Dientes"
WINDOW_CONTROLS = "Calibracion"
WINDOW_HELP = "Guia de Calibracion"

# Nombres cortos que aparecen literalmente sobre cada slider en OpenCV.
# El detalle completo de cada uno vive en _HELP_TEXT y se dibuja en la
# ventana WINDOW_HELP.
TB_BLUR = "1 Blur"
TB_OTSU = "2 Otsu automatico"
TB_THRESH = "3 Umbral manual"
TB_INVERT = "4 Invertir B/N"
TB_MORPH = "5 Morfologia"
TB_AREA = "6 Area minima x100"
TB_SHAPE_ON = "7 Filtro engrane ON"
TB_CIRC = "8 Circularidad min x100"
TB_SOLID = "9 Solidez min x100"
TB_SMOOTH = "10 Suavizado perfil"
TB_PROM = "11 Prominencia pico"
TB_DIST = "12 Distancia entre dientes"
TB_BRIGHTNESS = "0 Brillo ESP32"

_HELP_TEXT = [
    ("PREPROCESAMIENTO (limpian la imagen antes de buscar el engrane)", None),
    (TB_BLUR, "Difumina la imagen para quitar ruido/grano de la camara."
              " Muy bajo = ruido pasa como 'dientes' falsos. Muy alto = se"
              " pierden detalles finos de los dientes reales."),
    (TB_OTSU, "1 = el umbral de blanco/negro se calcula solo (recomendado"
              " con luz pareja). 0 = usas el slider de Umbral manual."),
    (TB_THRESH, "Solo aplica si Otsu=0. Todo pixel mas claro que este valor"
                " se vuelve blanco (objeto), el resto negro (fondo)."),
    (TB_INVERT, "Si tu engrane sale negro sobre fondo blanco en vez de al"
                " reves, activa esto (1) para invertir la mascara."),
    (TB_MORPH, "Cierra huecos pequenos y quita puntos sueltos de la"
               " mascara binaria. Si el contorno del engrane se ve"
               " fragmentado, sube este valor."),
    (TB_AREA, "Tamano minimo (en cientos de pixeles^2) que debe tener un"
              " objeto para considerarlo. Subelo para ignorar objetos"
              " chicos (ruido, dedos en la esquina, etc)."),
    ("FILTRO DE FORMA (evita detectar personas u otros objetos)", None),
    (TB_SHAPE_ON, "1 = solo acepta objetos cuya forma (redondez, solidez,"
                  " proporcion ancho/alto) se parece a un engrane."
                  " 0 = desactivado (acepta cualquier forma, no recomendado)."),
    (TB_CIRC, "Que tan 'redondo' debe verse el objeto (0-100 = 0.0-1.0 de"
              " circularidad). Una persona de pie da un valor bajo; un"
              " engrane da un valor medio-alto. Si rechaza tu engrane,"
              " bajalo un poco."),
    (TB_SOLID, "Que tan 'relleno' es el objeto respecto a su envolvente"
               " convexa (0-100 = 0.0-1.0). Los dientes del engrane restan"
               " poca solidez; una mano o un cuerpo restan mucha. Si"
               " rechaza tu engrane, bajalo un poco."),
    ("CONTEO DE DIENTES (perfil radial centro -> borde)", None),
    (TB_SMOOTH, "Suaviza el perfil radial para que el ruido del contorno no"
                " se cuente como dientes falsos. Muy alto = dientes reales"
                " se funden y se cuentan de menos."),
    (TB_PROM, "Que tan 'saliente' debe ser un pico para contarse como"
              " diente. Subelo si cuenta dientes de mas (ruido); bajalo si"
              " cuenta de menos (dientes muy pequenos)."),
    (TB_DIST, "Separacion minima entre dos dientes consecutivos. Subelo si"
              " un mismo diente se cuenta dos veces."),
]


class DisplayApp:
    def __init__(self, camera: CameraSource, config: ToothCounterConfig, light_host: str = "camara.local"):
        self.camera = camera
        self.config = config
        self.service = ToothCounterService(config)
        self.light = LightController(host=light_host)
        self._help_panel_static = None  # se construye una sola vez (es texto fijo)

    # ------------------------------------------------------------------
    # Configuración de la ventana de trackbars (controles de calibración)
    # ------------------------------------------------------------------
    def _build_trackbars(self) -> None:
        cv2.namedWindow(WINDOW_CONTROLS, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW_CONTROLS, 480, 460)
        cfg = self.config

        cv2.createTrackbar(TB_BRIGHTNESS, WINDOW_CONTROLS, 255, 255, lambda v: None)
        cv2.createTrackbar(TB_BLUR, WINDOW_CONTROLS, cfg.blur_kernel, 31, lambda v: None)
        cv2.createTrackbar(TB_OTSU, WINDOW_CONTROLS, int(cfg.use_otsu), 1, lambda v: None)
        cv2.createTrackbar(TB_THRESH, WINDOW_CONTROLS, cfg.manual_threshold, 255, lambda v: None)
        cv2.createTrackbar(TB_INVERT, WINDOW_CONTROLS, int(cfg.invert_binary), 1, lambda v: None)
        cv2.createTrackbar(TB_MORPH, WINDOW_CONTROLS, cfg.morph_kernel, 31, lambda v: None)
        cv2.createTrackbar(TB_AREA, WINDOW_CONTROLS, cfg.min_contour_area // 100, 500, lambda v: None)
        cv2.createTrackbar(TB_SHAPE_ON, WINDOW_CONTROLS, int(cfg.use_shape_filter), 1, lambda v: None)
        cv2.createTrackbar(TB_CIRC, WINDOW_CONTROLS, int(cfg.min_circularity * 100), 100, lambda v: None)
        cv2.createTrackbar(TB_SOLID, WINDOW_CONTROLS, int(cfg.min_solidity * 100), 100, lambda v: None)
        cv2.createTrackbar(TB_SMOOTH, WINDOW_CONTROLS, cfg.smoothing_window, 51, lambda v: None)
        cv2.createTrackbar(TB_PROM, WINDOW_CONTROLS, int(cfg.peak_prominence), 100, lambda v: None)
        cv2.createTrackbar(TB_DIST, WINDOW_CONTROLS, cfg.peak_min_distance, 100, lambda v: None)

        cv2.namedWindow(WINDOW_HELP, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WINDOW_HELP, 620, 760)

    def _read_trackbars_into_config(self) -> None:
        esp_brightness = cv2.getTrackbarPos(TB_BRIGHTNESS, WINDOW_CONTROLS)
        self.light.set_brightness(esp_brightness)
        cfg = self.config
        cfg.blur_kernel = max(1, cv2.getTrackbarPos(TB_BLUR, WINDOW_CONTROLS))
        cfg.use_otsu = bool(cv2.getTrackbarPos(TB_OTSU, WINDOW_CONTROLS))
        cfg.manual_threshold = cv2.getTrackbarPos(TB_THRESH, WINDOW_CONTROLS)
        cfg.invert_binary = bool(cv2.getTrackbarPos(TB_INVERT, WINDOW_CONTROLS))
        cfg.morph_kernel = cv2.getTrackbarPos(TB_MORPH, WINDOW_CONTROLS)
        cfg.min_contour_area = max(100, cv2.getTrackbarPos(TB_AREA, WINDOW_CONTROLS) * 100)
        cfg.use_shape_filter = bool(cv2.getTrackbarPos(TB_SHAPE_ON, WINDOW_CONTROLS))
        cfg.min_circularity = cv2.getTrackbarPos(TB_CIRC, WINDOW_CONTROLS) / 100.0
        cfg.min_solidity = cv2.getTrackbarPos(TB_SOLID, WINDOW_CONTROLS) / 100.0
        cfg.smoothing_window = max(1, cv2.getTrackbarPos(TB_SMOOTH, WINDOW_CONTROLS))
        cfg.peak_prominence = max(0.1, float(cv2.getTrackbarPos(TB_PROM, WINDOW_CONTROLS)))
        cfg.peak_min_distance = max(1, cv2.getTrackbarPos(TB_DIST, WINDOW_CONTROLS))

    # ------------------------------------------------------------------
    # Panel de ayuda (texto fijo con la explicación de cada slider)
    # ------------------------------------------------------------------
    def _build_help_panel(self) -> np.ndarray:
        panel = np.full((900, 620, 3), 30, dtype=np.uint8)
        y = 30
        for name, desc in _HELP_TEXT:
            if desc is None:
                cv2.putText(panel, name, (15, y), cv2.FONT_HERSHEY_SIMPLEX,
                            0.55, (0, 200, 255), 2)
                y += 26
                continue
            cv2.putText(panel, name, (25, y), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (255, 255, 255), 1)
            y += 20
            for line in self._wrap_text(desc, 62):
                cv2.putText(panel, line, (30, y), cv2.FONT_HERSHEY_SIMPLEX,
                            0.42, (180, 180, 180), 1)
                y += 18
            y += 8
        return panel

    @staticmethod
    def _wrap_text(text: str, max_chars: int):
        words = text.split()
        lines, current = [], ""
        for w in words:
            candidate = (current + " " + w).strip()
            if len(candidate) > max_chars:
                lines.append(current)
                current = w
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines

    def _draw_help_diagnostics(self, panel_base: np.ndarray, result: ToothDetectionResult) -> np.ndarray:
        """Agrega, debajo del texto fijo, un pequeño diagnóstico en vivo:
        la forma detectada (circularidad/solidez) del último objeto visto,
        para que el usuario sepa qué numeros comparar contra los sliders 8 y 9."""
        panel = panel_base.copy()
        y = panel.shape[0] - 90
        cv2.line(panel, (15, y - 15), (panel.shape[1] - 15, y - 15), (80, 80, 80), 1)
        cv2.putText(panel, "DIAGNOSTICO EN VIVO:", (15, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (0, 200, 255), 2)
        y += 24
        if result.shape_descriptors:
            d = result.shape_descriptors
            txt = (f"circularidad={d['circularity']:.2f}  "
                   f"solidez={d['solidity']:.2f}  "
                   f"aspecto={d['aspect_ratio']:.2f}")
            cv2.putText(panel, txt, (25, y), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, (200, 200, 200), 1)
        else:
            cv2.putText(panel, "(sin objeto detectado todavia)", (25, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (150, 150, 150), 1)
        return panel

    # ------------------------------------------------------------------
    # Dibujo de resultados sobre el frame
    # ------------------------------------------------------------------
    @staticmethod
    def _draw_result(frame: np.ndarray, result: ToothDetectionResult, fps: float) -> np.ndarray:
        output = frame.copy()

        if result.success:
            cv2.drawContours(output, [result.contour], -1, (0, 255, 0), 2)
            cv2.circle(output, result.centroid, 5, (255, 0, 0), -1)
            for px, py in result.peak_points:
                cv2.circle(output, (px, py), 4, (0, 0, 255), -1)

            text = f"Dientes detectados: {result.tooth_count}"
            color = (0, 255, 0) if not result.warning else (0, 165, 255)
        else:
            text = result.message
            color = (0, 0, 255)

        cv2.putText(output, text, (15, 35), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)

        if result.success and result.warning:
            cv2.putText(output, result.warning, (15, 65), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (0, 165, 255), 1)

        cv2.putText(
            output, f"FPS: {fps:.1f}", (15, output.shape[0] - 45),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2,
        )
        cv2.putText(
            output, "Presiona 'q' para salir | 's' para guardar captura",
            (15, output.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1,
        )
        return output

    # ------------------------------------------------------------------
    # Bucle principal
    # ------------------------------------------------------------------
    def run(self) -> None:
        self.camera.open()
        self._build_trackbars()
        self._help_panel_static = self._build_help_panel()

        prev_time = time.time()
        snapshot_count = 0

        try:
            while True:
                ok, frame = self.camera.read()
                if not ok or frame is None:
                    print("No se pudo leer el frame de la cámara.")
                    break

                self._read_trackbars_into_config()
                result = self.service.analyze(frame)

                now = time.time()
                fps = 1.0 / max(1e-6, now - prev_time)
                prev_time = now

                output = self._draw_result(frame, result, fps)
                cv2.imshow(WINDOW_MAIN, output)

                help_panel = self._draw_help_diagnostics(self._help_panel_static, result)
                cv2.imshow(WINDOW_HELP, help_panel)

                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord("s"):
                    snapshot_count += 1
                    filename = f"captura_{snapshot_count}.png"
                    cv2.imwrite(filename, output)
                    print(f"Captura guardada: {filename}")
        finally:
            self.light.stop()
            self.camera.release()
            cv2.destroyAllWindows()
