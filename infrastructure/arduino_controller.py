"""
Capa de INFRAESTRUCTURA.

Cliente serie (USB) para el Arduino que controla la banda transportadora,
el sensor de distancia (VL53L0X), la luz y el servo clasificador. Ver
arduino_banda/arduino_banda.ino para el firmware y el detalle de por qué
se reasignaron los pines (conflicto Servo/Timer1 con analogWrite en los
pines 9 y 10).

PROTOCOLO (texto plano, una instrucción por línea, 115200 baudios):

  Arduino -> PC:
    READY               firmware listo (sensor inicializado correctamente)
    ERROR:VL53L0X        no se pudo inicializar el sensor de distancia
    EVENT:DETECTADO      se detectó una pieza; la banda ya se detuvo
    EVENT:TIMEOUT        no llegó RESULTADO:... a tiempo; la banda se
                         reanudó sola, sin mover el servo

  PC -> Arduino:
    LUZ:<0-255>          intensidad de la luz
    RESULTADO:APROBADO   clasificación final de la pieza detenida: el
    RESULTADO:DEFECTUOSO Arduino mueve el servo hacia ese lado y reanuda
                         la banda

Igual que las fuentes de cámara (DroidCamSource/IPCameraSource), abrir y
cerrar es no bloqueante: un hilo de fondo mantiene la conexión (y
reintenta solo si el Arduino se desconecta), lee líneas entrantes hacia
una cola de eventos que la GUI drena en cada tick, y envía luz/resultado
sin bloquear la interfaz.
"""
import queue
import threading
import time
from typing import List, Optional

import serial
from serial import SerialException

DEFAULT_ARDUINO_PORT = "COM3"
BAUD_RATE = 115200
READ_TIMEOUT_S = 0.2
RECONNECT_DELAY_S = 3.0
LIGHT_SEND_INTERVAL_S = 0.05
BOOT_SETTLE_S = 2.0  # el Arduino se reinicia solo al abrir el puerto serie


class ArduinoController:
    """Banda transportadora + sensor + luz + servo, por puerto serie."""

    def __init__(self, port: str = DEFAULT_ARDUINO_PORT, baud_rate: int = BAUD_RATE):
        self._port = (port or "").strip()
        self._baud_rate = baud_rate
        self._serial: Optional[serial.Serial] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._running = False
        self._connected = False
        self._last_error = ""
        self._events: "queue.Queue[str]" = queue.Queue()

        self._pending_light: Optional[int] = None
        self._last_light_sent: Optional[int] = None
        self._last_light_send_at = 0.0
        self._pending_result: Optional[str] = None

    def set_port(self, port: str) -> None:
        self._port = (port or "").strip()

    # ------------------------------------------------------------------
    def open(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        while self._running:
            ser = self._serial
            if ser is None:
                self._try_connect()
                if self._serial is None:
                    time.sleep(RECONNECT_DELAY_S)
                    continue
                ser = self._serial

            try:
                self._flush_pending_writes(ser)
                line = ser.readline()  # respeta READ_TIMEOUT_S del puerto
                if line:
                    text = line.decode("utf-8", errors="ignore").strip()
                    if text:
                        self._events.put(text)
            except (SerialException, OSError) as exc:
                with self._lock:
                    self._connected = False
                    self._last_error = f"Se perdió la conexión con el Arduino: {exc}"
                try:
                    ser.close()
                except Exception:
                    pass
                if self._serial is ser:
                    self._serial = None
                time.sleep(RECONNECT_DELAY_S)

    def _try_connect(self) -> None:
        if not self._port:
            with self._lock:
                self._last_error = "Escribe el puerto COM del Arduino para conectar."
            return
        try:
            ser = serial.Serial(self._port, self._baud_rate, timeout=READ_TIMEOUT_S)
            time.sleep(BOOT_SETTLE_S)
            self._serial = ser
            with self._lock:
                self._last_error = ""
                self._connected = True
            # fuerza reenvío del último valor de luz conocido al Arduino
            # recién (re)conectado, por si se había perdido la conexión.
            self._last_light_sent = None
        except (SerialException, OSError) as exc:
            with self._lock:
                self._last_error = (
                    f"No se pudo abrir el puerto '{self._port}': {exc}. "
                    "Revisa que el Arduino esté conectado, que el puerto sea el "
                    "correcto y que no esté abierto en el Monitor Serie del IDE. "
                    "Reintentando automáticamente..."
                )

    def _flush_pending_writes(self, ser: "serial.Serial") -> None:
        with self._lock:
            light = self._pending_light
            result = self._pending_result
            self._pending_result = None

        now = time.time()
        if (
            light is not None
            and light != self._last_light_sent
            and (now - self._last_light_send_at) >= LIGHT_SEND_INTERVAL_S
        ):
            ser.write(f"LUZ:{light}\n".encode("ascii"))
            self._last_light_sent = light
            self._last_light_send_at = now
            with self._lock:
                if self._pending_light == light:
                    self._pending_light = None

        if result is not None:
            ser.write(f"RESULTADO:{result}\n".encode("ascii"))

    # ------------------------------------------------------------------
    # Comandos hacia el Arduino (no bloqueantes: se encolan y las manda
    # el hilo de fondo en su próxima vuelta).
    # ------------------------------------------------------------------
    def send_light(self, value: int) -> None:
        value = max(0, min(255, int(value)))
        with self._lock:
            self._pending_light = value

    def send_result(self, quality: str) -> None:
        """quality: 'Aprobado' o 'Defectuoso' (tal cual los usa la GUI)."""
        code = "APROBADO" if quality == "Aprobado" else "DEFECTUOSO"
        with self._lock:
            self._pending_result = code

    # ------------------------------------------------------------------
    # Eventos desde el Arduino (READY, EVENT:DETECTADO, EVENT:TIMEOUT,
    # ERROR:VL53L0X, ...), en el orden en que llegaron.
    # ------------------------------------------------------------------
    def poll_events(self) -> List[str]:
        events: List[str] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                break
        return events

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
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
        with self._lock:
            self._connected = False

    def __enter__(self) -> "ArduinoController":
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()
