"""
Capa de INFRAESTRUCTURA.

Persiste la calibración píxeles->milímetros (pixels_per_mm) en un pequeño
archivo JSON local, para que no haya que recalibrar cada vez que se abre
la aplicación.
"""
import json
import os
from typing import Optional


class CalibrationRepository:
    def __init__(self, path: str = "calibracion.json"):
        self._path = path

    def load_pixels_per_mm(self) -> Optional[float]:
        if not os.path.isfile(self._path):
            return None
        with open(self._path, "r", encoding="utf-8") as f:
            data = json.load(f)
        value = data.get("pixels_per_mm")
        return float(value) if value else None

    def save_pixels_per_mm(self, value: float) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump({"pixels_per_mm": value}, f)
