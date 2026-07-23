"""
Capa de DOMINIO.

Aquí solo viven las estructuras de datos que representan el "concepto de negocio":
el resultado de analizar un engrane. Esta capa no sabe nada de OpenCV, cámaras
ni interfaces gráficas -> así se mantiene independiente y fácil de testear.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np


@dataclass
class ToothCounterConfig:
    """Parámetros ajustables del algoritmo de conteo de dientes.

    Se agrupan aquí para poder modificarlos en caliente (por ejemplo desde
    trackbars de la GUI) sin tocar la lógica del algoritmo.
    """

    # --- Preprocesamiento de imagen ---
    blur_kernel: int = 7           # tamaño del kernel de desenfoque (impar)
    use_otsu: bool = True          # usar umbral automático (Otsu)
    manual_threshold: int = 127    # umbral manual si use_otsu = False
    invert_binary: bool = False    # invertir blanco/negro tras el umbral
    morph_kernel: int = 5          # kernel para cerrar/abrir la máscara

    # --- Filtro de contornos por tamaño ---
    min_contour_area: int = 3000   # área mínima en px^2 para considerar un contorno

    # --- Filtro de FORMA: descarta objetos que no parecen engranaje ---
    # (personas, manos, cajas, etc. tienen circularidad/solidez muy distintas
    # a un engrane, que es básicamente un disco con muescas poco profundas).
    # Los rangos son deliberadamente laxos: un engrane con dientes grandes/
    # profundos o visto con un poco de perspectiva puede bajar bastante estos
    # valores, y antes de esto se estaba rechazando como "no parece engrane"
    # aunque la máscara B/N mostrara la silueta correcta.
    use_shape_filter: bool = True
    min_circularity: float = 0.03  # 4*pi*Area/Perimetro^2 -> 1.0 = círculo perfecto.
                                    # OJO: cv2.arcLength sobreestima MUCHO el perímetro en
                                    # contornos rasterizados (efecto "escalera" de píxeles),
                                    # y esto se agrava con muchos dientes o dientes profundos
                                    # (medido hasta ~0.036 en engranes sintéticos legítimos de
                                    # 36 dientes). Por eso este mínimo se deja casi como
                                    # pass-through: el filtro real contra personas/manos/cajas
                                    # lo hacen solidez y aspect ratio, que sí son estables.
    max_circularity: float = 0.98  # dientes profundos bajan mucho este valor, por
                                    # eso el minimo es laxo; el filtro fuerte es el aspect ratio
    min_solidity: float = 0.55     # Area / Area del casco convexo (dientes muy profundos y/o
                                    # numerosos restan bastante área frente al casco convexo;
                                    # 0.55 cubre engranes de dientes profundos sin abrir la
                                    # puerta a siluetas de manos/personas, que siguen quedando
                                    # fuera por el aspect ratio)
    max_solidity: float = 0.99
    min_aspect_ratio: float = 0.72  # ancho/alto del bounding box (~1 = redondo).
    max_aspect_ratio: float = 1.35  # Este es el filtro MAS fuerte contra personas/manos,
                                     # que suelen ser mucho mas altas que anchas (o viceversa).

    # --- Validación de rango de dientes esperado (avisa si algo "raro" se detecta) ---
    min_expected_teeth: int = 3
    max_expected_teeth: int = 200

    # --- Perfil radial (distancia centro -> borde por ángulo) ---
    resample_points: int = 720     # resolución angular del perfil (más = más preciso)
    smoothing_window: int = 9      # ventana de suavizado circular del perfil

    # --- Detección de picos (dientes) ---
    # peak_prominence es una FRACCIÓN del radio promedio del engrane detectado
    # (no píxeles absolutos): así "qué tan sobresaliente debe ser un diente"
    # se adapta solo al tamaño/zoom de la pieza en la imagen. Con un valor fijo
    # en píxeles, el mismo umbral era demasiado alto para un engrane chico o
    # lejos de la cámara (no detectaba ningún diente) y demasiado bajo para uno
    # grande/cerca (detectaba ruido como dientes).
    peak_prominence: float = 0.02
    peak_min_distance: int = 15    # separación mínima entre dientes (en puntos de muestreo)

    # --- Calibración de medida (píxeles -> milímetros) ---
    # None = sin calibrar todavía; se llena con el botón "Calibrar" de la GUI
    # (ver infrastructure/calibration_repository.py) y se persiste entre corridas.
    pixels_per_mm: Optional[float] = None


@dataclass
class ToothDetectionResult:
    """Resultado de analizar un solo frame de video."""

    success: bool
    tooth_count: int = 0
    centroid: Optional[Tuple[int, int]] = None
    contour: Optional[np.ndarray] = None
    peak_points: List[Tuple[int, int]] = field(default_factory=list)
    message: str = ""
    warning: str = ""              # aviso no bloqueante (p.ej. conteo fuera de rango esperado)
    shape_descriptors: Optional[dict] = None  # circularidad/solidez/aspecto del objeto detectado
