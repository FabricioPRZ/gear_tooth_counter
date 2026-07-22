"""
Capa de INFRAESTRUCTURA.

Envuelve un stream MJPEG de una cámara IP (p.ej. una ESP32-CAM corriendo el
firmware CameraWebServer, que expone el video en 'http://<host>:81/stream')
detrás de la misma interfaz simple (open/read/release) que CameraSource, de
forma que el resto del programa no necesita saber si la cámara es local o
remota.

TODO el trabajo de red (conectar, leer frames, reconectar si se cae) ocurre
en un hilo de fondo con timeouts cortos. open() solo lanza ese hilo y
regresa de inmediato: nunca bloquea al llamador (la GUI), ni siquiera si la
cámara está apagada o la IP es incorrecta. Si la conexión se pierde, el
propio hilo reintenta solo cada RECONNECT_DELAY_S segundos.
"""
import threading
import time
from typing import Optional, Tuple

import cv2
import numpy as np

DEFAULT_HOST = "camara.local"
# La ESP32-CAM de este proyecto sirve el HTML de bienvenida en el puerto 80
# ('http://camara.local') y el stream MJPEG real en el 81 (confirmado leyendo
# el <script> de esa página: src = 'http://'+location.hostname+':81/stream').
# IMPORTANTE: el firmware solo admite UN cliente de video a la vez — si el
# navegador tiene esa página abierta, esta app no podrá conectarse.
DEFAULT_STREAM_URL = f"http://{DEFAULT_HOST}:81/stream"
OPEN_TIMEOUT_MS = 4000
READ_TIMEOUT_MS = 4000
RECONNECT_DELAY_S = 3.0


class IPCameraSource:
    """Cliente de un stream MJPEG remoto (ESP32-CAM u otra cámara IP), con
    conexión y reconexión automáticas en segundo plano."""

    def __init__(self, url: str):
        self._url = url
        self._cap: Optional[cv2.VideoCapture] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._running = False
        self._connected = False
        self._last_error = ""

    def open(self) -> None:
        """Lanza el hilo de conexión/lectura y regresa de inmediato. La
        conexión real (y sus posibles fallos/timeouts) ocurre en segundo
        plano, así que esto nunca traba la GUI."""
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
                    self._last_error = f"Se perdió la señal de '{self._url}'."
                cap.release()
                if self._cap is cap:
                    self._cap = None
                time.sleep(RECONNECT_DELAY_S)

    def _try_connect(self) -> None:
        # CAP_PROP_OPEN_TIMEOUT_MSEC / READ_TIMEOUT_MSEC evitan que
        # VideoCapture se quede esperando indefinidamente a una cámara
        # apagada o inalcanzable.
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
                    f"No se pudo conectar a '{self._url}'. "
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

    # Permite usar la clase con "with IPCameraSource(url) as cam:"
    def __enter__(self) -> "IPCameraSource":
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()

    @staticmethod
    def build_stream_url(ip_or_url: str) -> str:
        """Acepta una URL completa ('http://camara.local/stream.mjpg') o
        solo un host/IP; en este último caso antepone 'http://' pero NO
        asume puerto ni ruta (cada cámara/firmware expone el MJPEG en un
        path distinto). Si se deja vacío, usa DEFAULT_HOST."""
        value = (ip_or_url or "").strip() or DEFAULT_HOST
        if value.startswith("http://") or value.startswith("https://"):
            return value
        return f"http://{value}"
