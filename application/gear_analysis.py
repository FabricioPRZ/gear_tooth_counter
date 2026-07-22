"""
Capa de APLICACIÓN.

Funciones auxiliares que enriquecen el resultado del conteo de dientes para
el reporte de inspección: a qué tipo de engrane corresponde, qué tan grande
es (en mm, si hay calibración) y si aparenta tener corrosión. Son funciones
puras sobre el contorno/frame ya detectados por ToothCounterService; no
saben nada de la GUI ni del reporte.

CLASIFICACIÓN POR TIPO:
Se basa únicamente en el número de dientes, usando los rangos que se usan
en el taller. Es una heurística simple (no un catálogo real de engranes),
pensada para dar una etiqueta útil en el reporte sin requerir un dataset.

MEDICIÓN:
Se usa el círculo mínimo que encierra el contorno (cv2.minEnclosingCircle)
como diámetro de referencia del engrane en píxeles. Para convertirlo a
milímetros hace falta calibrar una vez con una pieza de diámetro conocido
(ver infrastructure/calibration_repository.py y el botón "Calibrar" de la
GUI): pixels_per_mm = diametro_px_pieza_conocida / diametro_mm_real.

DETECCIÓN DE CORROSIÓN:
Heurística por color: cuenta qué porcentaje de los píxeles DENTRO del
contorno caen en un rango de tono/saturación típico del óxido (café,
naranja, rojizo). No es un modelo entrenado, así que puede fallar con
iluminación extraña o manchas de otro origen (pintura, grasa oscura,
etc.) — es una primera aproximación, no un diagnóstico certero.
"""
from typing import NamedTuple, Optional

import cv2
import numpy as np

# --- Clasificación por rango de dientes (ajustar aquí si el taller usa otros) ---
_GEAR_TYPE_RANGES = [
    (3, 12, "Piñón pequeño"),
    (13, 30, "Engrane mediano"),
    (31, 60, "Engrane grande"),
    (61, 10_000, "Engrane industrial"),
]

# --- Heurística de color para óxido (en espacio HSV de OpenCV: H 0-179) ---
_RUST_HSV_LOWER = np.array([4, 60, 40], dtype=np.uint8)
_RUST_HSV_UPPER = np.array([25, 255, 220], dtype=np.uint8)
_RUST_RATIO_THRESHOLD = 0.08  # 8% del área del engrane con color tipo óxido


def classify_gear_type(tooth_count: int) -> str:
    for low, high, label in _GEAR_TYPE_RANGES:
        if low <= tooth_count <= high:
            return label
    return "Indeterminado"


def measure_diameter_px(contour: np.ndarray) -> float:
    _, radius = cv2.minEnclosingCircle(contour)
    return 2.0 * radius


def diameter_px_to_mm(diameter_px: float, pixels_per_mm: Optional[float]) -> Optional[float]:
    if not pixels_per_mm or pixels_per_mm <= 0:
        return None
    return diameter_px / pixels_per_mm


class CorrosionResult(NamedTuple):
    has_corrosion: bool
    rust_ratio: float  # 0.0 - 1.0, porcentaje del área del engrane con color tipo óxido


def detect_corrosion(frame_bgr: np.ndarray, contour: np.ndarray) -> CorrosionResult:
    mask = np.zeros(frame_bgr.shape[:2], dtype=np.uint8)
    cv2.drawContours(mask, [contour], -1, 255, thickness=cv2.FILLED)

    gear_area = int(cv2.countNonZero(mask))
    if gear_area == 0:
        return CorrosionResult(has_corrosion=False, rust_ratio=0.0)

    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    rust_mask = cv2.inRange(hsv, _RUST_HSV_LOWER, _RUST_HSV_UPPER)
    rust_mask = cv2.bitwise_and(rust_mask, rust_mask, mask=mask)

    rust_pixels = int(cv2.countNonZero(rust_mask))
    ratio = rust_pixels / gear_area
    return CorrosionResult(has_corrosion=ratio >= _RUST_RATIO_THRESHOLD, rust_ratio=ratio)
