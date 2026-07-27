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

DECISIÓN AUTOMÁTICA DE DEFECTUOSO/APROBADO (analyze_defects):
Combina tres señales, cada una independiente entre sí, para decidir si la
pieza se marca como "Defectuoso" SIN intervención del usuario, explicando
siempre el POR QUÉ (para que el operario pueda verificar a simple vista):

  1. Conteo de dientes fuera del rango esperado (ya calculado por
     ToothCounterService, viene en result.warning) -> probable ruido o
     pieza distinta a la esperada.
  2. Corrosión detectada por color (ver detect_corrosion).
  3. Diente roto o faltante: se mira el ángulo (respecto al centroide) de
     cada pico/diente detectado y se calcula el hueco angular más grande
     entre dos dientes consecutivos. En un engrane sano los dientes están
     parejos, así que todos los huecos son parecidos al esperado
     (360°/n_dientes). Si un diente se rompió o falta, ese hueco se dispara
     muy por encima del resto -> lo marcamos como sospechoso.

Si CUALQUIERA de las tres señales se dispara, la pieza se marca defectuosa
y se listan las razones concretas (no solo "Defectuoso" a secas).
"""
import math
from typing import List, NamedTuple, Optional, Tuple

import cv2
import numpy as np

from domain.models import ToothCounterConfig, ToothDetectionResult

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
_RUST_RATIO_THRESHOLD = 0.08  # valor por defecto; ver cfg.rust_ratio_threshold


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


def detect_corrosion(
    frame_bgr: np.ndarray,
    contour: np.ndarray,
    ratio_threshold: float = _RUST_RATIO_THRESHOLD,
) -> CorrosionResult:
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
    return CorrosionResult(has_corrosion=ratio >= ratio_threshold, rust_ratio=ratio)


class DefectAnalysis(NamedTuple):
    is_defective: bool
    reasons: List[str]  # explicaciones legibles, una por cada señal de defecto disparada


def _largest_angular_gap_deg(
    peak_points: List[Tuple[int, int]], centroid: Tuple[int, int]
) -> Optional[Tuple[float, float]]:
    """Devuelve (hueco_mas_grande_grados, hueco_esperado_grados) entre dientes
    consecutivos, o None si no hay suficientes dientes para que el cálculo
    tenga sentido (hace falta al menos 3 para hablar de "huecos parejos")."""
    if len(peak_points) < 3:
        return None

    cx, cy = centroid
    angles = sorted(
        math.atan2(py - cy, px - cx) for px, py in peak_points
    )
    gaps = [angles[i + 1] - angles[i] for i in range(len(angles) - 1)]
    gaps.append((angles[0] + 2 * math.pi) - angles[-1])

    expected = 2 * math.pi / len(angles)
    largest = max(gaps)
    return math.degrees(largest), math.degrees(expected)


def analyze_defects(
    result: ToothDetectionResult,
    corrosion: CorrosionResult,
    cfg: ToothCounterConfig,
) -> DefectAnalysis:
    """Decide automáticamente si la pieza es Defectuoso/Aprobado, explicando
    siempre el motivo concreto de cada señal que se dispara. Solo tiene
    sentido llamarla cuando result.success es True (ya hay un engrane
    detectado con dientes contados)."""
    reasons: List[str] = []

    if result.warning:
        reasons.append(result.warning)

    if corrosion.has_corrosion:
        reasons.append(
            f"Corrosión detectada: {corrosion.rust_ratio * 100:.1f}% del área "
            f"de la pieza tiene tono de óxido (umbral {cfg.rust_ratio_threshold * 100:.0f}%)."
        )

    gap_info = _largest_angular_gap_deg(result.peak_points, result.centroid)
    if gap_info is not None:
        largest_gap_deg, expected_gap_deg = gap_info
        if largest_gap_deg >= expected_gap_deg * cfg.tooth_gap_ratio_threshold:
            reasons.append(
                "Posible diente roto o faltante: se encontró un hueco de "
                f"{largest_gap_deg:.0f}° entre dos dientes consecutivos, cuando "
                f"lo esperado por espaciado uniforme es ~{expected_gap_deg:.0f}°."
            )

    return DefectAnalysis(is_defective=bool(reasons), reasons=reasons)
