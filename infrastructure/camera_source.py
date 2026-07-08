"""
Capa de INFRAESTRUCTURA.

Encapsula el acceso a un dispositivo físico (la webcam) detrás de una
interfaz simple. El día que se quiera cambiar la webcam por el módulo de
cámara del ESP32-CAM (que entrega frames por HTTP/MJPEG), solo se necesita
crear otra clase con la misma forma (open/read/release) y el resto del
programa no se entera del cambio.
"""
from typing import Optional, Tuple

import cv2
import numpy as np


class CameraSource:
    """Envuelve cv2.VideoCapture para la webcam local."""

    def __init__(self, index: int = 0, width: int = 1280, height: int = 720):
        self._index = index
        self._width = width
        self._height = height
        self._cap: Optional[cv2.VideoCapture] = None

    def open(self) -> None:
        self._cap = cv2.VideoCapture(self._index)
        if not self._cap.isOpened():
            raise RuntimeError(
                f"No se pudo abrir la cámara con índice {self._index}. "
                "Verifica que no esté siendo usada por otra aplicación "
                "o prueba con otro índice (0, 1, 2...)."
            )
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if self._cap is None:
            raise RuntimeError("La cámara no ha sido abierta. Llama a open() primero.")
        ok, frame = self._cap.read()
        return ok, frame

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    # Permite usar la clase con "with CameraSource() as cam:"
    def __enter__(self) -> "CameraSource":
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()
