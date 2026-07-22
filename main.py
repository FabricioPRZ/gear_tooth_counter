"""
Punto de entrada del sistema de conteo de dientes de engrane.

Ejecutar con:
    python main.py

Requisitos (ver requirements.txt):
    pip install -r requirements.txt

La interfaz es una GUI de Tkinter (presentation/tkinter_app.py): permite
ingresar la IP de una cámara ESP32-CAM, conectarse y ver el conteo de
dientes en vivo. Para la versión anterior con ventanas de OpenCV y
trackbars de calibración, ver presentation/display_window.py.
"""
from domain.models import ToothCounterConfig
from presentation.tkinter_app import TkinterApp


def main() -> None:
    config = ToothCounterConfig()  # valores por defecto del algoritmo de conteo

    app = TkinterApp(config=config)
    app.run()


if __name__ == "__main__":
    main()
