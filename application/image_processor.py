"""
Capa de APLICACIÓN (parte 1/2).

Funciones puras de procesamiento de imagen: convierten un frame a color en
una máscara binaria y extraen el contorno principal (el engrane). No
dependen de la cámara ni de la GUI, por lo que son fáciles de probar con
imágenes sueltas.

FILTRO DE FORMA (nuevo):
Como no usamos una red neuronal, no "sabemos" qué es un engrane de forma
semántica. Lo que sí podemos hacer es describir su FORMA con 3 números que
son muy distintos para un engrane vs. una persona, una mano o una caja:

  - circularidad = 4*pi*Area / Perimetro^2
        Un círculo perfecto da 1.0. Un engrane (disco con dientes) da un
        valor un poco más bajo porque el perímetro crece por los dientes,
        pero sigue siendo "redondeado" (típicamente 0.55 - 0.95).
        Una persona de pie da un valor bajo (silueta alargada e irregular).

  - solidez = Area / Area del casco convexo (convex hull)
        Un engrane tiene muescas poco profundas entre dientes, así que su
        área es casi igual a la de su "envolvente convexa" (0.80 - 0.98).
        Una persona con brazos separados, dedos, etc. tiene mucha más
        diferencia entre su silueta real y su casco convexo -> solidez baja.

  - aspect ratio = ancho_bbox / alto_bbox
        Un engrane visto de frente es prácticamente redondo -> ratio ~1.
        Una persona de pie es mucho más alta que ancha -> ratio bajo.

Si el contorno más grande de la imagen no cumple estos 3 criterios, se
descarta aunque sea grande, y el sistema informa que no detectó un engrane.
"""
from typing import Optional, Tuple

import cv2
import numpy as np

from domain.models import ToothCounterConfig


def to_binary_mask(frame_bgr: np.ndarray, cfg: ToothCounterConfig) -> np.ndarray:
    """Convierte un frame BGR en una máscara binaria (0/255) del objeto."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

    k = cfg.blur_kernel if cfg.blur_kernel % 2 == 1 else cfg.blur_kernel + 1
    blurred = cv2.GaussianBlur(gray, (k, k), 0)

    if cfg.use_otsu:
        _, binary = cv2.threshold(
            blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
    else:
        _, binary = cv2.threshold(
            blurred, cfg.manual_threshold, 255, cv2.THRESH_BINARY
        )

    if cfg.invert_binary:
        binary = cv2.bitwise_not(binary)

    if cfg.morph_kernel > 0:
        m = cfg.morph_kernel if cfg.morph_kernel % 2 == 1 else cfg.morph_kernel + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (m, m))
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    return binary


def compute_shape_descriptors(contour: np.ndarray) -> dict:
    """Calcula circularidad, solidez y aspect ratio de un contorno."""
    area = cv2.contourArea(contour)
    perimeter = cv2.arcLength(contour, True)
    circularity = (4 * np.pi * area / (perimeter ** 2)) if perimeter > 0 else 0.0

    hull = cv2.convexHull(contour)
    hull_area = cv2.contourArea(hull)
    solidity = (area / hull_area) if hull_area > 0 else 0.0

    _, _, w, h = cv2.boundingRect(contour)
    aspect_ratio = (w / h) if h > 0 else 0.0

    return {
        "area": area,
        "perimeter": perimeter,
        "circularity": circularity,
        "solidity": solidity,
        "aspect_ratio": aspect_ratio,
    }


def _looks_like_gear(descriptors: dict, cfg: ToothCounterConfig) -> bool:
    if not cfg.use_shape_filter:
        return True
    return (
        cfg.min_circularity <= descriptors["circularity"] <= cfg.max_circularity
        and cfg.min_solidity <= descriptors["solidity"] <= cfg.max_solidity
        and cfg.min_aspect_ratio <= descriptors["aspect_ratio"] <= cfg.max_aspect_ratio
    )


def find_gear_contour(
    binary_mask: np.ndarray, cfg: ToothCounterConfig
) -> Tuple[Optional[np.ndarray], str, Optional[dict]]:
    """Busca, entre todos los contornos de la máscara, el que más parece un
    engrane (tamaño suficiente + forma redondeada tipo disco dentado).

    Devuelve (contorno, mensaje, descriptores_de_forma). El contorno es
    None si no se encontró nada válido; el mensaje explica por qué.
    """
    contours, _ = cv2.findContours(
        binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
    )
    if not contours:
        return None, "No se detectó ningún objeto en la imagen.", None

    candidates = [c for c in contours if cv2.contourArea(c) >= cfg.min_contour_area]
    if not candidates:
        return None, "No hay objetos suficientemente grandes (revisa 'Area minima').", None

    scored = [(c, compute_shape_descriptors(c)) for c in candidates]

    gear_like = [item for item in scored if _looks_like_gear(item[1], cfg)]

    if not gear_like:
        # Reportamos los descriptores del objeto más grande descartado para
        # que el usuario entienda POR QUÉ se rechazó y pueda recalibrar.
        biggest = max(scored, key=lambda item: item[1]["area"])
        return (
            None,
            "Se detectó un objeto pero su forma no parece un engrane "
            "(revisa el panel de calibracion / puede ser una persona u otro objeto).",
            biggest[1],
        )

    best = max(gear_like, key=lambda item: item[1]["area"])
    return best[0], "OK", best[1]
