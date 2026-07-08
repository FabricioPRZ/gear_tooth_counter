# Contador de Dientes de Engrane — Visión Artificial

Sistema de control de calidad que usa visión por computadora clásica (sin
redes neuronales, sin dataset de entrenamiento) para contar los dientes de
un engrane en tiempo real desde una cámara, como base para un sistema que
determine si una pieza **cumple** o **no cumple** con lo requerido.

Esta primera versión usa la webcam de la computadora. Está diseñada para
que, más adelante, la fuente de video se pueda reemplazar por un módulo de
cámara de un ESP32-CAM sin tocar el algoritmo ni la interfaz.

## Tabla de contenido

- [Cómo funciona el algoritmo](#cómo-funciona-el-algoritmo)
- [Filtro de forma (evita detectar personas u otros objetos)](#filtro-de-forma-evita-detectar-personas-u-otros-objetos)
- [Arquitectura del proyecto](#arquitectura-del-proyecto)
- [Instalación](#instalación)
- [Uso](#uso)
- [Guía de calibración paso a paso](#guía-de-calibración-paso-a-paso)
- [Referencia de sliders](#referencia-de-sliders)
- [Limitaciones conocidas](#limitaciones-conocidas)
- [Próximos pasos sugeridos](#próximos-pasos-sugeridos)

## Cómo funciona el algoritmo

1. **Binarización**: el frame de la cámara se convierte a escala de grises,
   se desenfoca (para quitar ruido) y se umbraliza (Otsu automático o
   manual) para obtener una máscara blanco/negro del objeto.
2. **Contorno**: se extraen los contornos externos de la máscara y se
   descartan los que son demasiado pequeños.
3. **Filtro de forma**: de los contornos restantes, se descartan los que no
   tienen "forma de engrane" (ver siguiente sección). Esto evita que una
   persona, una mano o cualquier otro objeto redondeado grande sea
   contado como si fuera la pieza.
4. **Centroide**: se calcula el centro de masa del contorno del engrane.
5. **Perfil radial**: para cada punto del contorno se mide su ángulo y su
   distancia respecto al centroide. Esto genera una curva `r(θ)` — qué tan
   lejos está el borde del centro en cada dirección.
6. **Suavizado**: se suaviza esa curva de forma circular (theta = -π y
   theta = +π son el mismo punto) para eliminar ruido de la imagen.
7. **Conteo de picos**: cada diente del engrane se ve como un máximo local
   en el perfil. Se cuentan con `scipy.signal.find_peaks` → ese número de
   picos es el número de dientes.

Es un enfoque explicable, robusto y que no requiere entrenar ningún
modelo, ideal para una primera versión de un sistema de control de
calidad industrial.

## Filtro de forma (evita detectar personas u otros objetos)

Como no se usa una red neuronal, el sistema no "sabe" semánticamente qué es
un engrane. Lo que sí puede hacer es describir la **forma** del contorno
con tres números que son muy distintos entre un engrane y una persona,
mano u otro objeto:

| Descriptor | Fórmula | Qué mide |
|---|---|---|
| **Circularidad** | `4·π·Area / Perímetro²` | Qué tan redondo es. 1.0 = círculo perfecto. Los dientes bajan este valor un poco. |
| **Solidez** | `Area / Area del casco convexo` | Qué tan "relleno" está el objeto respecto a su envolvente convexa. Los dientes restan poca solidez; una mano o un cuerpo con brazos separados restan mucha. |
| **Aspect ratio** | `ancho_bbox / alto_bbox` | Un engrane visto de frente es prácticamente cuadrado (~1). Una persona de pie es mucho más alta que ancha. Es el filtro **más fuerte** contra personas. |

Solo se acepta como "engrane" el contorno más grande cuyos tres valores
caigan dentro de los rangos configurados (sliders 7-9 en la ventana de
calibración). Si nada cumple, el sistema informa que no detectó un
engrane en vez de forzar un conteo sobre un objeto equivocado.

> **Nota honesta:** esto es un filtro heurístico, no un clasificador
> entrenado. Es muy efectivo contra siluetas de personas, manos, cajas,
> etc., pero un objeto redondo y compacto (un plato, una pelota, otra
> pieza circular) podría pasar el filtro. Para una línea de producción
> real, se recomienda además fijar una región de interés (ROI) donde solo
> debería aparecer la pieza a inspeccionar.

## Arquitectura del proyecto

Arquitectura limpia por capas, cada una con una única responsabilidad:

```
gear_tooth_counter/
├── domain/
│   └── models.py              # ToothCounterConfig, ToothDetectionResult
│                               # (estructuras de datos, sin OpenCV)
├── application/
│   ├── image_processor.py     # binarización + filtro de forma (funciones puras)
│   └── tooth_counter_service.py  # algoritmo de perfil radial y conteo de picos
├── infrastructure/
│   └── camera_source.py       # acceso a la webcam (cv2.VideoCapture)
├── presentation/
│   └── display_window.py      # ventanas de video, trackbars y guía de calibración
├── main.py                    # punto de entrada
└── requirements.txt
```

- **domain** no depende de nada (ni siquiera de OpenCV): son solo
  estructuras de datos, fáciles de testear.
- **application** contiene el algoritmo puro: recibe imágenes (`numpy
  arrays`) y configuración, y devuelve resultados. No sabe de cámaras ni
  de GUI.
- **infrastructure** encapsula el acceso a la webcam detrás de una
  interfaz simple (`open/read/release`). El día que se reemplace por un
  ESP32-CAM (que entrega frames por HTTP/MJPEG), solo se crea una clase
  nueva con la misma forma y el resto del programa no se entera del
  cambio.
- **presentation** solo dibuja y lee la configuración de los trackbars; no
  contiene lógica de visión por computadora.

## Instalación

Requiere Python 3.9+.

```bash
cd gear_tooth_counter
pip install -r requirements.txt
```

Dependencias: `opencv-python`, `numpy`, `scipy`.

## Uso

```bash
python main.py
```

Se abren 3 ventanas:

1. **Control de Calidad - Conteo de Dientes**: el video en vivo con el
   contorno del engrane (verde), su centroide (azul) y cada diente
   detectado (rojo), más el conteo total.
2. **Calibracion**: los sliders para ajustar el algoritmo en vivo.
3. **Guia de Calibracion**: la explicación de cada slider y un
   diagnóstico en vivo (circularidad/solidez/aspecto del último objeto
   visto), para saber qué valores comparar contra los sliders del filtro
   de forma.

Controles de teclado sobre la ventana de video:
- `q` → salir
- `s` → guardar una captura (`captura_N.png`) del frame actual

## Guía de calibración paso a paso

1. Coloca el engrane sobre un fondo con buen contraste (idealmente liso y
   con iluminación pareja).
2. Deja `Otsu automatico = 1` primero y observa si el contorno verde
   envuelve bien el engrane en el video principal.
   - Si el engrane sale negro sobre fondo blanco (o al revés de lo
     esperado), activa `Invertir B/N`.
3. Si el fondo tiene ruido o el contorno se ve fragmentado, sube **Blur**
   y **Morfologia** poco a poco.
4. Si aparece "no parece un engrane" en rojo, abre la ventana **Guia de
   Calibracion**: ahí verás la circularidad/solidez/aspecto detectados.
   Ajusta los sliders 8 (`Circularidad min`) y 9 (`Solidez min`) hasta que
   tu engrane entre en rango. Prueba también parándote frente a la cámara
   o poniendo tu mano: debería seguir diciendo "no parece un engrane".
5. Ajusta **Suavizado**, **Prominencia** y **Distancia entre dientes**
   (sliders 10-12) hasta que la cantidad de puntos rojos coincida con los
   dientes reales del engrane (ni de más por ruido, ni de menos por
   sobre-suavizado).

## Referencia de sliders

| # | Slider | Qué hace |
|---|---|---|
| 1 | Blur | Desenfoca la imagen para quitar ruido antes de binarizar. |
| 2 | Otsu automatico | 1 = umbral calculado solo (recomendado con luz pareja). 0 = usa el slider 3. |
| 3 | Umbral manual | Umbral fijo de blanco/negro (solo si Otsu = 0). |
| 4 | Invertir B/N | Invierte la máscara si el objeto sale del color equivocado. |
| 5 | Morfologia | Cierra huecos y quita puntos sueltos de la máscara binaria. |
| 6 | Area minima x100 | Tamaño mínimo (en cientos de px²) para considerar un objeto. |
| 7 | Filtro engrane ON | Activa/desactiva el filtro de forma (recomendado: activado). |
| 8 | Circularidad min x100 | Qué tan redondo debe verse el objeto (0-100 = 0.0-1.0). |
| 9 | Solidez min x100 | Qué tan "relleno" debe estar el objeto respecto a su casco convexo. |
| 10 | Suavizado | Suaviza el perfil radial para evitar contar ruido como dientes. |
| 11 | Prominencia pico | Qué tan saliente debe ser un pico para contarse como diente. |
| 12 | Distancia entre dientes | Separación mínima entre dos dientes para no contar el mismo dos veces. |

## Limitaciones conocidas

- El filtro de forma es heurístico, no un clasificador entrenado: un
  objeto redondo y compacto que no sea un engrane podría pasar el filtro.
- Depende de buena iluminación y contraste entre el engrane y el fondo.
- El conteo de dientes es sensible a los sliders 10-12; en piezas con
  dientes muy pequeños o muy juntos requiere recalibrar.
- Pensado para una pieza a la vez, vista de frente (no en ángulo).

## Próximos pasos sugeridos

- Definir una región de interés (ROI) fija donde debe aparecer la pieza,
  para reforzar aún más el filtro de forma.
- Agregar la lógica de "cumple / no cumple" comparando el conteo contra un
  número de dientes esperado por tipo de pieza.
- Reemplazar `CameraSource` por una clase equivalente que reciba frames
  del módulo de cámara del ESP32-CAM vía HTTP/MJPEG, sin tocar el resto
  del sistema.