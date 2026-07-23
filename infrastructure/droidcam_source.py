"""
Capa de INFRAESTRUCTURA.

Cliente del stream de video que expone DroidCam (o Iriun Webcam) por WiFi.
No requiere cable USB ni el cliente de escritorio de DroidCam instalado en
la PC: basta con que el celular esté en la misma red y se indiquen su IP y
puerto (la app DroidCam los muestra en pantalla al elegir conexión WiFi; el
puerto por defecto es 4747). Con eso se arma la URL 'http://<ip>:<puerto>/
video', que es el feed MJPEG que la propia app expone.

Misma interfaz no bloqueante que IPCameraSource (open/read/is_connected/
last_error/release), para que la GUI pueda tratar ambas fuentes de cámara
de forma intercambiable sin importarle cuál está activa.
"""
import threading
import time
from typing import Optional, Tuple

import cv2
import numpy as np

DEFAULT_PORT = 4747
OPEN_TIMEOUT_MS = 4000
READ_TIMEOUT_MS = 4000
RECONNECT_DELAY_S = 3.0


class DroidCamSource:
    """Cámara del celular vía DroidCam/Iriun Webcam por WiFi, identificada
    por su IP y puerto (sin cable, sin cliente de escritorio)."""

    def __init__(self, ip: str, port: int = DEFAULT_PORT):
        self._ip = (ip or "").strip()
        self._port = port
        self._url = self.build_url(self._ip, self._port)
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
                        f"Se perdió la señal de DroidCam en '{self._url}'."
                    )
                cap.release()
                if self._cap is cap:
                    self._cap = None
                time.sleep(RECONNECT_DELAY_S)

    def _try_connect(self) -> None:
        if not self._ip:
            with self._lock:
                self._last_error = "Ingresa la IP del celular para conectar por WiFi."
            return
        # CAP_PROP_OPEN_TIMEOUT_MSEC / READ_TIMEOUT_MSEC evitan que
        # VideoCapture se quede esperando indefinidamente a un celular
        # apagado o inalcanzable.
        cap = cv2.VideoCapture(self._url, cv2.CAP_FFMPEG, [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT_MS,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_TIMEOUT_MS,
        ])
        if cap.isOpened():
            self._cap = cap
            with self._lock:
                self._last_error = ""
        else:
            cap.release()
            with self._lock:
                self._last_error = (
                    f"No se pudo conectar a DroidCam en '{self._url}'. "
                    "Verifica que el celular esté en la misma red WiFi y que "
                    "la app DroidCam esté abierta. Reintentando automáticamente..."
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

    def __enter__(self) -> "DroidCamSource":
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()

    @staticmethod
    def build_url(ip: str, port: int = DEFAULT_PORT) -> str:
        """Arma la URL del feed de DroidCam a partir de la IP y el puerto
        que muestra la app en el celular al conectarse por WiFi."""
        host = (ip or "").strip()
        return f"http://{host}:{port}/video"
