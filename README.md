# Contador de Dientes de Engrane — Visión Artificial

Sistema de control de calidad que usa visión por computadora clásica (sin
redes neuronales, sin dataset de entrenamiento) para contar los dientes de
un engrane en tiempo real, decidir automáticamente si la pieza está
**Aprobada** o **Defectuosa**, y dirigirla físicamente a su contenedor
correspondiente en una banda transportadora.

**Arquitectura física actual:**

- **Cámara**: la cámara del celular por WiFi, usando la app DroidCam (ya
  no se usa ESP32-CAM: se retiró por completo).
- **Arduino** (Uno/Nano): controla la banda transportadora (motor vía
  driver L298N), un sensor de distancia (VL53L0X) que detecta cuándo llega
  un engrane, la luz que ilumina la pieza (PWM), y un servo que dirige la
  pieza ya clasificada hacia el contenedor de Aprobado o Defectuoso.
- **PC (esta interfaz en Python)**: analiza la imagen de la cámara,
  cuenta los dientes, clasifica la pieza, y le manda el resultado al
  Arduino por USB/serie para que mueva el servo y reanude la banda.

Flujo completo: el sensor del Arduino detecta el engrane → la banda se
detiene → la PC clasifica la pieza con la cámara → la PC le contesta al
Arduino → el servo dirige la pieza y la banda se reanuda sola. Ver la
sección [Conectar el Arduino con la interfaz](#conectar-el-arduino-con-la-interfaz)
para el detalle de cableado, firmware y protocolo.

## Tabla de contenido

- [Cómo funciona el algoritmo](#cómo-funciona-el-algoritmo)
- [Filtro de forma (evita detectar personas u otros objetos)](#filtro-de-forma-evita-detectar-personas-u-otros-objetos)
- [Arquitectura del proyecto](#arquitectura-del-proyecto)
- [Instalación](#instalación)
- [Uso](#uso)
- [Conectar el Arduino con la interfaz](#conectar-el-arduino-con-la-interfaz)
- [Guía de calibración paso a paso](#guía-de-calibración-paso-a-paso)
- [Referencia de controles de detección](#referencia-de-controles-de-detección)
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
caigan dentro de los rangos configurados (`min/max_circularity`,
`min/max_solidity`, `min/max_aspect_ratio` en `domain/models.py`, con el
checkbox **Filtro de forma** del panel de detección para activarlo o
desactivarlo). Si nada cumple, el sistema informa que no detectó un
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
│   └── models.py                    # ToothCounterConfig, ToothDetectionResult
│                                     # (estructuras de datos, sin OpenCV)
├── application/
│   ├── image_processor.py           # binarización + filtro de forma (funciones puras)
│   ├── tooth_counter_service.py     # algoritmo de perfil radial y conteo de picos
│   └── gear_analysis.py             # tipo/medida/corrosión y decisión Aprobado/Defectuoso
├── infrastructure/
│   ├── droidcam_source.py           # cámara del celular por WiFi (DroidCam/Iriun)
│   ├── arduino_controller.py        # banda/sensor/luz/servo por puerto serie (USB)
│   ├── calibration_repository.py    # persistencia de la calibración px->mm
│   └── record_repository.py         # guardado de registros en el reporte Excel
├── presentation/
│   └── tkinter_app.py               # interfaz gráfica (Tkinter) — la app actual
├── arduino_banda/
│   └── arduino_banda.ino            # firmware del Arduino (banda+sensor+luz+servo)
├── main.py                          # punto de entrada
└── requirements.txt
```

- **domain** no depende de nada (ni siquiera de OpenCV): son solo
  estructuras de datos, fáciles de testear.
- **application** contiene el algoritmo puro: recibe imágenes (`numpy
  arrays`) y configuración, y devuelve resultados. No sabe de cámaras ni
  de GUI.
- **infrastructure** encapsula el acceso a la cámara y al Arduino detrás
  de interfaces simples y no bloqueantes (`open/read (o poll)/
  is_connected/last_error/release`), cada una en su propio hilo de fondo.
- **presentation** (`tkinter_app.py`) solo dibuja, lee la configuración de
  los controles y orquesta el flujo cámara → clasificación → Arduino; no
  contiene lógica de visión por computadora.

> **Nota:** `infrastructure/ip_camera_source.py`, `infrastructure/light_controller.py`
> y `esp32_luz/` quedaron del diseño anterior basado en ESP32-CAM y ya
> **no se usan** en la app actual (`main.py` → `presentation/tkinter_app.py`).
> Se dejaron en el repositorio solo porque `presentation/display_window.py`
> (una interfaz alternativa antigua, basada en ventanas de OpenCV, que
> tampoco se usa desde `main.py`) todavía los referencia.

## Instalación

Requiere Python 3.9+.

```bash
cd gear_tooth_counter
pip install -r requirements.txt
```

Dependencias: `opencv-python`, `numpy`, `scipy`, `Pillow`, `openpyxl`,
`pyserial` (esta última para hablar con el Arduino por USB).

Además, en el celular: instala la app **DroidCam** (Android/iOS) para
exponer su cámara por WiFi. Y en la PC, para programar el Arduino:
**Arduino IDE** con las librerías `Adafruit VL53L0X` y `Servo` (ver
[Conectar el Arduino con la interfaz](#conectar-el-arduino-con-la-interfaz)).

## Uso

```bash
python main.py
```

Se abre una sola ventana (interfaz Tkinter) que se maximiza sola a la
pantalla disponible:

- **Panel izquierdo — Cámara en tiempo real**: video en vivo del celular
  (DroidCam) con el contorno del engrane (verde), su centroide (azul) y
  cada diente detectado (rojo). Incluye:
  - Campos **IP celular** / **Puerto** y el botón **Conectar/Desconectar**
    para la cámara.
  - Campo **Arduino (puerto COM)** y botón **Conectar/Desconectar** para
    la banda/sensor/servo, con un indicador de conexión (punto verde/rojo).
  - Slider **Intensidad de luz**, que ahora se manda al Arduino por el
    mismo puerto serie (antes iba por HTTP a una ESP32 aparte).
- **Panel derecho — Lectura actual**: número de dientes, tipo de engrane,
  diámetro (si hay calibración px→mm), corrosión aparente, la calidad
  **Aprobado/Defectuoso** (automática, con el motivo explicado si se
  rechaza; el usuario puede sobrescribirla con los botones), el
  mini-calibrador de medida, el botón para guardar el registro en el
  reporte Excel, y — más abajo, con scroll si la pantalla es chica — los
  controles de calibración de la detección (binarización, filtro de forma).

El indicador **CÁMARA CONECTADA/DESCONECTADA** (arriba a la derecha) es
para la cámara del celular. El estado del Arduino se ve en el punto junto
a su campo de puerto COM, en el panel de cámara.

## Conectar el Arduino con la interfaz

El Arduino se conecta a la PC por **cable USB** (es también su alimentación
para la lógica; el motor y el servo, si consumen más corriente, deben
llevar su propia fuente externa — ver cableado abajo). La PC y el Arduino
se hablan por el **puerto serie** a través de ese mismo cable USB: no hace
falta WiFi ni Bluetooth para esta parte.

### 1. Cablea el Arduino

El firmware (`arduino_banda/arduino_banda.ino`) espera este cableado:

| Componente | Pin Arduino | Notas |
|---|---|---|
| Sensor VL53L0X — SDA | A4 | bus I2C |
| Sensor VL53L0X — SCL | A5 | bus I2C |
| Sensor VL53L0X — VIN / GND | 5V / GND | |
| Driver L298N — ENA (velocidad, PWM) | D5 | **reasignado desde el pin 9 original** |
| Driver L298N — IN1 | D7 | dirección del motor |
| Driver L298N — IN2 | D8 | dirección del motor |
| Luz (PWM, a un mosfet/driver) | D6 | antes se controlaba por HTTP desde una ESP32 aparte |
| Servo clasificador — señal | D9 | pin liberado por el cambio de ENA |

> **¿Por qué se movió el pin del motor (ENA) del 9 al 5?** En un Arduino
> Uno/Nano, la librería `Servo.h` usa internamente el **Timer1** del chip
> para generar los pulsos del servo. Ese es el mismo temporizador que
> `analogWrite()` usa para generar PWM en los pines **9 y 10** — en cuanto
> se activa un servo, el PWM por hardware de esos dos pines deja de
> funcionar bien, sin importar en qué pin esté conectado el servo. El
> código original de la banda usaba el pin 9 para la velocidad del motor,
> así que se reasignó al pin 5 (Timer0, sin conflicto) para poder agregar
> el servo sin romper el control de velocidad. Si modificas el cableado,
> evita usar `analogWrite()` en los pines 9 o 10 mientras haya un servo
> conectado.

Alimenta el motor (a través del L298N) y el servo con una fuente externa
adecuada a su consumo — no tires de esa corriente del pin 5V del Arduino.
Comparte el GND entre el Arduino, el L298N, la fuente externa y el servo.

### 2. Flashea el firmware

1. Abre `arduino_banda/arduino_banda.ino` en el Arduino IDE.
2. Instala las librerías `Adafruit VL53L0X` y `Servo` desde el
   Administrador de Bibliotecas (Servo ya viene incluida con el IDE).
3. Selecciona tu placa (Uno/Nano) y el puerto COM, y sube el sketch.
4. Abre el Monitor Serie a 115200 baudios: al arrancar deberías ver
   `READY`. Si ves `ERROR:VL53L0X`, revisa el cableado I2C del sensor
   (no arranca la banda hasta que el sensor responda, por seguridad).
5. **Cierra el Monitor Serie** antes de usar la interfaz de Python: un
   puerto serie solo lo puede tener abierto un programa a la vez.

### 3. Conecta el Arduino desde la interfaz

1. Con el Arduino ya programado y conectado por USB, anota su puerto COM
   (Administrador de dispositivos en Windows, o `ls /dev/tty*` en
   Linux/Mac).
2. Abre la interfaz (`python main.py`), escribe ese puerto en el campo
   **Arduino (puerto COM)** del panel de cámara (por defecto trae
   `COM3`, el más común en Windows, pero cámbialo si el tuyo es otro) y
   pulsa **Conectar**. El punto junto al campo se pone verde cuando la
   conexión está viva.
3. El slider **Intensidad de luz** ya queda controlando la luz del
   Arduino.

### 4. Protocolo (por si necesitas depurarlo)

Texto plano, una instrucción por línea, 115200 baudios:

| Dirección | Mensaje | Significado |
|---|---|---|
| Arduino → PC | `READY` | firmware listo (sensor inicializado) |
| Arduino → PC | `ERROR:VL53L0X` | no se pudo inicializar el sensor de distancia |
| Arduino → PC | `EVENT:DETECTADO` | se detectó una pieza; la banda ya se detuvo |
| Arduino → PC | `EVENT:TIMEOUT` | no llegó `RESULTADO:...` a tiempo (15 s); la banda se reanudó sola, sin mover el servo |
| PC → Arduino | `LUZ:<0-255>` | intensidad de la luz |
| PC → Arduino | `RESULTADO:APROBADO` | mueve el servo hacia el contenedor de piezas buenas y reanuda la banda |
| PC → Arduino | `RESULTADO:DEFECTUOSO` | mueve el servo hacia el contenedor de piezas defectuosas y reanuda la banda |

### 5. Flujo automático de inspección

Una vez todo conectado, el ciclo es automático:

1. El sensor del Arduino detecta un engrane sobre la banda → el Arduino
   frena el motor y manda `EVENT:DETECTADO`.
2. La interfaz espera un instante (a que la imagen deje de moverse),
   analiza el frame de la cámara del celular y decide Aprobado/Defectuoso
   con el mismo criterio automático que se muestra en pantalla (ver
   `application/gear_analysis.py`).
3. La interfaz le contesta al Arduino (`RESULTADO:...`), este mueve el
   servo hacia el contenedor correcto, vuelve al centro, y reanuda la
   banda solo.
4. Si la cámara no logra ver la pieza a tiempo, el Arduino se cansa de
   esperar (timeout de seguridad) y reanuda la banda sin clasificar, para
   que la línea nunca se quede trabada — la interfaz avisa de esto en el
   panel de retroalimentación.

Puedes seguir usando el botón **Agregar registro** para guardar la lectura
en el reporte Excel cuando quieras; ese guardado sigue siendo manual y es
independiente del movimiento físico del servo/banda.

## Guía de calibración paso a paso

1. Coloca el engrane sobre un fondo con buen contraste (idealmente liso y
   con iluminación pareja) y ajusta el slider **Intensidad de luz**.
2. Deja **Umbral automático (Otsu)** activado primero y observa si el
   contorno verde envuelve bien el engrane en el video.
   - Si el engrane sale negro sobre fondo blanco (o al revés de lo
     esperado), activa **Invertir B/N**.
3. Si el fondo tiene ruido o el contorno se ve fragmentado, sube
   **Desenfoque** y **Morfología** poco a poco.
4. Si aparece "no parece un engrane" en rojo sobre el video, fíjate en las
   líneas de diagnóstico que se dibujan debajo (circularidad/solidez/
   aspecto detectados vs. el rango válido configurado). Esos rangos
   (`min_circularity`, `min_solidity`, etc.) no tienen slider en esta
   versión de la interfaz — se ajustan directamente en
   `domain/models.py` si tu engrane necesita un rango distinto al que
   trae por defecto. Prueba también parándote frente a la cámara o
   poniendo tu mano: debería seguir diciendo "no parece un engrane".
5. Activa **Ver máscara B/N** para ver exactamente lo que ve el
   algoritmo (blanco = objeto, negro = fondo) — la forma más rápida de
   entender por qué algo no se detecta bien.

## Referencia de controles de detección

Controles disponibles en el panel derecho, sección "CALIBRACIÓN DE
DETECCIÓN":

| Control | Qué hace |
|---|---|
| Ver máscara B/N | Muestra la máscara binaria en vez del video normal: exactamente lo que ve el algoritmo antes de buscar el contorno. |
| Umbral automático (Otsu) | Activado = el umbral de blanco/negro se calcula solo (recomendado con luz pareja). Desactivado = usa el slider **Umbral**. |
| Invertir B/N | Si el engrane sale del color equivocado (negro sobre blanco en vez de al revés), actívalo para invertir la máscara. |
| Filtro de forma | Activa/desactiva el filtro de circularidad/solidez/aspecto que evita detectar personas u otros objetos (recomendado: activado). |
| Umbral | Umbral manual de blanco/negro (solo aplica si Otsu está desactivado). |
| Desenfoque | Difumina la imagen para quitar ruido antes de binarizar. |
| Morfología | Cierra huecos y quita puntos sueltos de la máscara binaria. |
| Área mín. x100 | Tamaño mínimo (en cientos de px²) para considerar un objeto. |

Los parámetros más finos del algoritmo (rangos de circularidad/solidez/
aspecto del filtro de forma, suavizado del perfil radial, prominencia y
distancia mínima entre picos/dientes, y los umbrales de la detección
automática de defectos) no están expuestos como sliders en esta interfaz;
viven como valores por defecto, ya documentados y afinados, en
`domain/models.py` (`ToothCounterConfig`).

## Limitaciones conocidas

- El filtro de forma es heurístico, no un clasificador entrenado: un
  objeto redondo y compacto que no sea un engrane podría pasar el filtro.
- Depende de buena iluminación y contraste entre el engrane y el fondo.
- El conteo de dientes depende de los parámetros de suavizado/prominencia/
  distancia entre picos (`domain/models.py`); en piezas con dientes muy
  pequeños o muy juntos puede requerir ajustarlos.
- Pensado para una pieza a la vez, vista de frente (no en ángulo).
- El umbral de detección del sensor (`DISTANCIA_DELTA_MM` en
  `arduino_banda.ino`, 30 mm por defecto) y el tiempo de espera antes de
  clasificar (`CLASSIFY_SETTLE_S` en `tkinter_app.py`) están pensados para
  una banda a velocidad moderada; con bandas más rápidas o piezas muy
  chicas puede hacer falta afinarlos.
- El timeout de seguridad del Arduino (`SAFETY_TIMEOUT_MS`, 15 s) reanuda
  la banda sin clasificar si la PC no contesta a tiempo — es intencional
  (evita que la línea se trabe), pero esa pieza no queda registrada.

## Próximos pasos sugeridos

- Definir una región de interés (ROI) fija donde debe aparecer la pieza,
  para reforzar aún más el filtro de forma.
- Calibrar en planta los ángulos del servo (`ANGULO_APROBADO`/
  `ANGULO_DEFECTUOSO` en `arduino_banda.ino`) y la distancia umbral del
  sensor según la posición real de la banda y los contenedores.
- Registrar automáticamente en el reporte Excel cada pieza que pasa por
  la banda (hoy el guardado con "Agregar registro" es manual), para tener
  trazabilidad de todas las piezas y no solo las que el operario decide
  guardar.
- Retirar del repositorio `infrastructure/ip_camera_source.py`,
  `infrastructure/light_controller.py`, `esp32_luz/` y
  `presentation/display_window.py` una vez se confirme que ya no hace
  falta la interfaz alternativa basada en ESP32-CAM/OpenCV.