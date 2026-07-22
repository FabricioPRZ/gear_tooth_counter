"""
Capa de INFRAESTRUCTURA.

Cliente HTTP para la ESP32 que controla la intensidad de la luz (ver
esp32_luz/esp32_luz.ino). El envío ocurre en un hilo de fondo para que
mover el slider de la GUI nunca se sienta trabado esperando a la red: cada
nuevo valor reemplaza al anterior si todavía no se había enviado (así,
durante un arrastre rápido del slider, solo se manda el último valor).
"""
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

DEFAULT_LIGHT_HOST = "luz.local"
SEND_TIMEOUT_S = 2.0
POLL_INTERVAL_S = 0.05


class LightController:
    def __init__(self, host: str = DEFAULT_LIGHT_HOST):
        self._lock = threading.Lock()
        self._host = host
        self._pending: Optional[int] = None
        self._last_sent: Optional[int] = None
        self._last_error = ""
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def set_host(self, host: str) -> None:
        host = (host or "").strip() or DEFAULT_LIGHT_HOST
        with self._lock:
            if host != self._host:
                self._host = host
                self._last_sent = None  # fuerza reenvío del valor actual al nuevo host

    def set_brightness(self, value: int) -> None:
        value = max(0, min(255, int(value)))
        with self._lock:
            self._pending = value

    def _worker(self) -> None:
        while self._running:
            with self._lock:
                value = self._pending
                host = self._host
                self._pending = None
            if value is not None and value != self._last_sent:
                self._send(host, value)
            time.sleep(POLL_INTERVAL_S)

    def _send(self, host: str, value: int) -> None:
        url = f"http://{host}/brillo?valor={value}"
        try:
            urllib.request.urlopen(url, timeout=SEND_TIMEOUT_S)
            self._last_sent = value
            with self._lock:
                self._last_error = ""
        except (urllib.error.URLError, OSError) as exc:
            with self._lock:
                self._last_error = f"No se pudo enviar el brillo a '{host}': {exc}"

    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    def stop(self) -> None:
        self._running = False
        self._thread.join(timeout=1.0)
