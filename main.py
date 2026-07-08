"""
Punto de entrada del sistema de conteo de dientes de engrane.

Ejecutar con:
    python main.py

Requisitos (ver requirements.txt):
    pip install -r requirements.txt
"""
from domain.models import ToothCounterConfig
from infrastructure.camera_source import CameraSource
from presentation.display_window import DisplayApp


def main() -> None:
    # Índice 0 = primera cámara disponible del sistema (webcam por defecto).
    # Si tienes varias cámaras y no abre la correcta, prueba 1, 2, etc.
    camera = CameraSource(index=0, width=1280, height=720)

    config = ToothCounterConfig()  # valores por defecto, ajustables con trackbars

    app = DisplayApp(camera=camera, config=config)
    app.run()


if __name__ == "__main__":
    main()
