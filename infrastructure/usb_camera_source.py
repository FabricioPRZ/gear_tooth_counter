"""
Capa de INFRAESTRUCTURA.

Cliente de una cámara local expuesta como dispositivo de video estándar de
Linux (/dev/videoN) — típicamente el celular conectado por cable USB
corriendo una app como DroidCam o Iriun Webcam, que lo presenta como una
webcam más. Se usa como respaldo cuando la ESP32-CAM no da buena imagen o
no conecta.

Misma interfaz no bloqueante que IPCameraSource (open/read/is_connected/
last_error/release), para que la GUI pueda tratar ambas fuentes de cámara
de forma intercambiable sin importarle cuál está activa.
"""
import threading
import time
from typing import Optional, Tuple

import cv2
import numpy as np

RECONNECT_DELAY_S = 3.0


class USBCameraSource:
    """Cámara local (webcam integrada o celular vía DroidCam/Iriun Webcam
    por USB) identificada por su índice de dispositivo (0, 1, 2...)."""

    def __init__(self, device_index: int = 0):
        self._device_index = device_index
        self._cap: Optional[cv2.VideoCapture] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._running = False
        self._connected = False
        self._last_error = ""

    def open(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        while self._running:
            cap = self._cap
            if cap is None:
                self._try_connect()
                if self._cap is None:
                    time.sleep(RECONNECT_DELAY_S)
                    continue
                cap = self._cap

            ok, frame = cap.read()
            if not self._running:
                break  # release() pudo haber corrido mientras read() bloqueaba
            if ok and frame is not None:
                with self._lock:
                    self._latest_frame = frame
                    self._connected = True
            else:
                with self._lock:
                    self._connected = False
                    self._last_error = (
                        f"Se perdió la señal de la cámara USB (índice {self._device_index})."
                    )
                cap.release()
                if self._cap is cap:
                    self._cap = None
                time.sleep(RECONNECT_DELAY_S)

    def _try_connect(self) -> None:
        cap = cv2.VideoCapture(self._device_index)
        if cap.isOpened():
            self._cap = cap
            with self._lock:
                self._last_error = ""
        else:
            cap.release()
            with self._lock:
                self._last_error = (
                    f"No se encontró cámara USB en el índice {self._device_index}. "
                    "Reintentando automáticamente..."
                )

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        with self._lock:
            if self._latest_frame is None:
                return False, None
            return True, self._latest_frame.copy()

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    def release(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._connected = False

    def __enter__(self) -> "USBCameraSource":
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()
