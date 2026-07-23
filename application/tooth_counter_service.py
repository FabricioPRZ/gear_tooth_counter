"""
Capa de APLICACIÓN (parte 2/2). Aquí vive el algoritmo de conteo de dientes.

IDEA DEL ALGORITMO (visión por computadora clásica, sin red neuronal):
1. Se binariza la imagen y se obtiene el contorno externo del engrane
   (aplicando además un filtro de forma que descarta objetos que no
   parecen un engrane, ver image_processor.py).
2. Se calcula el centroide del contorno.
3. Para cada punto del contorno se calcula:
      - el ángulo respecto al centroide (0-360°)
      - la distancia (radio) al centroide
   Esto genera un "perfil radial" r(theta): qué tan lejos está el borde
   del centro en cada dirección.
4. En ese perfil, cada DIENTE del engrane se ve como un PICO (un máximo
   local), y cada valle entre dientes es un mínimo local.
5. Se suaviza el perfil (para quitar ruido de la imagen) y se cuentan los
   picos con scipy.signal.find_peaks -> ese número de picos es el número
   de dientes.

Es un enfoque robusto, explicable e independiente de dataset/entrenamiento,
ideal para una primera versión de un sistema de control de calidad.
"""
from typing import List, Tuple

import cv2
import numpy as np
from scipy.signal import find_peaks

from domain.models import ToothCounterConfig, ToothDetectionResult
from application.image_processor import to_binary_mask, find_gear_contour


class ToothCounterService:
    def __init__(self, config: ToothCounterConfig):
        self.config = config

    def analyze(self, frame_bgr: np.ndarray) -> ToothDetectionResult:
        cfg = self.config

        binary = to_binary_mask(frame_bgr, cfg)
        contour, contour_message, shape_desc = find_gear_contour(binary, cfg)

        if contour is None:
            return ToothDetectionResult(
                success=False,
                message=contour_message,
                shape_descriptors=shape_desc,
            )

        centroid = self._centroid(contour)
        if centroid is None:
            return ToothDetectionResult(
                success=False,
                message="No se pudo calcular el centroide del contorno.",
            )

        angles, radii = self._radial_profile(contour, centroid, cfg.resample_points)
        radii_smooth = self._smooth_circular(radii, cfg.smoothing_window)

        # cfg.peak_prominence es una fracción del radio promedio (ver
        # domain/models.py): así el umbral de "qué tan saliente debe ser un
        # diente" se adapta solo al tamaño real del engrane en la imagen, en
        # vez de exigir el mismo valor en píxeles sin importar el zoom.
        mean_radius = float(np.mean(radii_smooth)) if len(radii_smooth) else 0.0
        prominence_px = max(1.0, cfg.peak_prominence * mean_radius)

        peak_indices = self._find_circular_peaks(
            radii_smooth, cfg.peak_min_distance, prominence_px
        )

        peak_points = self._angles_to_points(
            angles[peak_indices], radii_smooth[peak_indices], centroid
        )

        tooth_count = len(peak_indices)
        warning = ""
        if not (cfg.min_expected_teeth <= tooth_count <= cfg.max_expected_teeth):
            warning = (
                f"Conteo ({tooth_count}) fuera del rango esperado "
                f"[{cfg.min_expected_teeth}-{cfg.max_expected_teeth}]. "
                "Puede ser ruido o un objeto redondo que no es un engrane."
            )

        return ToothDetectionResult(
            success=True,
            tooth_count=tooth_count,
            centroid=centroid,
            contour=contour,
            peak_points=peak_points,
            message="OK",
            warning=warning,
            shape_descriptors=shape_desc,
        )

    # ------------------------------------------------------------------
    # Métodos internos del algoritmo
    # ------------------------------------------------------------------

    @staticmethod
    def _centroid(contour: np.ndarray) -> Tuple[int, int]:
        m = cv2.moments(contour)
        if m["m00"] == 0:
            return None
        cx = int(m["m10"] / m["m00"])
        cy = int(m["m01"] / m["m00"])
        return cx, cy

    @staticmethod
    def _radial_profile(
        contour: np.ndarray, centroid: Tuple[int, int], num_points: int
    ) -> Tuple[np.ndarray, np.ndarray]:
        cx, cy = centroid
        pts = contour.reshape(-1, 2).astype(np.float64)

        angles = np.arctan2(pts[:, 1] - cy, pts[:, 0] - cx)
        radii = np.sqrt((pts[:, 0] - cx) ** 2 + (pts[:, 1] - cy) ** 2)

        # Ordenar por ángulo para poder interpolar en una malla uniforme
        order = np.argsort(angles)
        angles_sorted = angles[order]
        radii_sorted = radii[order]

        grid = np.linspace(-np.pi, np.pi, num_points, endpoint=False)
        radii_interp = np.interp(
            grid, angles_sorted, radii_sorted, period=2 * np.pi
        )
        return grid, radii_interp

    @staticmethod
    def _smooth_circular(profile: np.ndarray, window: int) -> np.ndarray:
        if window <= 1:
            return profile
        kernel = np.ones(window) / window
        padded = np.pad(profile, (window, window), mode="wrap")
        smoothed = np.convolve(padded, kernel, mode="same")
        return smoothed[window:-window]

    @staticmethod
    def _find_circular_peaks(
        profile: np.ndarray, min_distance: int, prominence: float
    ) -> np.ndarray:
        """find_peaks no entiende que el perfil es circular (theta=-pi y
        theta=+pi son el mismo punto), así que lo triplicamos y nos
        quedamos solo con los picos que caen en la copia central."""
        n = len(profile)
        tiled = np.concatenate([profile, profile, profile])

        peaks, _ = find_peaks(
            tiled, distance=max(1, min_distance), prominence=prominence
        )

        mask = (peaks >= n) & (peaks < 2 * n)
        return peaks[mask] - n

    @staticmethod
    def _angles_to_points(
        angles: np.ndarray, radii: np.ndarray, centroid: Tuple[int, int]
    ) -> List[Tuple[int, int]]:
        cx, cy = centroid
        xs = cx + radii * np.cos(angles)
        ys = cy + radii * np.sin(angles)
        return list(zip(xs.astype(int), ys.astype(int)))
