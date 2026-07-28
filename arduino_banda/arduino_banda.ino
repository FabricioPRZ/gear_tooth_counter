/*
 * ESTACIÓN DE INSPECCIÓN - CONTROL DE BANDA, SENSOR, LUZ Y SERVO
 * =================================================================
 * Este Arduino (Uno/Nano, ATmega328) reemplaza por completo a la
 * ESP32-CAM: la única cámara del sistema es ahora el celular por WiFi
 * (DroidCam), y este Arduino se encarga de todo lo físico:
 *
 *   1. Sensor ultrasónico/ToF (VL53L0X) que detecta cuándo llega un
 *      engrane sobre la banda.
 *   2. Motor de la banda transportadora (driver L298N): la detiene en
 *      cuanto detecta una pieza.
 *   3. Luz PWM para iluminar la pieza (antes controlada por HTTP desde
 *      una ESP32 aparte; ahora se controla por el mismo puerto serie).
 *   4. Servo que dirige la pieza ya clasificada hacia el contenedor de
 *      "Aprobado" o "Defectuoso", y reanuda la banda.
 *
 * FLUJO COMPLETO (el que pidió el taller):
 *   Sensor detecta pieza -> Arduino frena la banda -> avisa a la PC
 *   ("EVENT:DETECTADO") -> la PC (interfaz Python) analiza el frame de
 *   la cámara del celular y decide Aprobado/Defectuoso -> le manda el
 *   resultado al Arduino ("RESULTADO:APROBADO"/"RESULTADO:DEFECTUOSO")
 *   -> el Arduino mueve el servo hacia ese lado, vuelve al centro, y
 *   reanuda la banda.
 *
 * Si por algún motivo la PC nunca contesta (cámara desconectada, no se
 * ve la pieza, etc.), hay un timeout de seguridad (SAFETY_TIMEOUT_MS)
 * que reanuda la banda solo, sin mover el servo, para que la línea no
 * se quede trabada esperando para siempre.
 *
 * ---------------------------------------------------------------
 * ¡IMPORTANTE! CAMBIO DE PINES RESPECTO AL CÓDIGO ORIGINAL DE LA BANDA
 * ---------------------------------------------------------------
 * En el Arduino Uno/Nano, la librería Servo.h usa el Timer1 por dentro
 * para generar los pulsos del servo. Ese es EL MISMO temporizador que
 * analogWrite() usa para generar PWM en los pines 9 y 10: en cuanto se
 * hace servo.attach(...) (sin importar en qué pin quede conectado el
 * servo), el PWM por hardware de los pines 9 y 10 deja de funcionar
 * bien. El código original de la banda usaba PIN_ENA = 9 (velocidad
 * del motor por PWM), lo cual chocaría con el servo.
 *
 * Por eso aquí se reasignaron los pines así:
 *   - ENA (velocidad del motor): pin 9  ->  pin 5   (Timer0, sin choque)
 *   - LUZ (PWM):                  nuevo ->  pin 6   (Timer0, sin choque)
 *   - SERVO:                      nuevo ->  pin 9   (libre ahora)
 *   - IN1 / IN2 (dirección del motor, sin PWM): se quedan igual (7 y 8)
 *
 * Si vuelves a cablear el proyecto, NO uses analogWrite() en los pines
 * 9 o 10 mientras el servo esté conectado.
 * ---------------------------------------------------------------
 *
 * CONEXIONES:
 *   VL53L0X  -> SDA a A4, SCL a A5 (I2C), VIN a 5V, GND a GND.
 *               (En un Nano/Uno son los mismos pines A4/A5; en otras
 *               placas usa los pines SDA/SCL dedicados si existen).
 *   L298N    -> ENA a D5, IN1 a D7, IN2 a D8, GND común con el Arduino.
 *               Alimenta el motor y el L298N con su fuente externa, NO
 *               con el 5V del Arduino.
 *   Luz PWM  -> D6 a la entrada de control del driver/mosfet de la luz.
 *   Servo    -> señal a D9, alimentación e IN de PC (5V/GND idealmente
 *               desde una fuente externa, no del pin 5V del Arduino si
 *               el servo es de cierta potencia).
 *
 * LIBRERÍAS NECESARIAS (Arduino IDE -> Administrador de Bibliotecas):
 *   - "Adafruit VL53L0X" (de Adafruit)
 *   - "Servo" (viene incluida con el IDE)
 *
 * PROTOCOLO SERIE (115200 baudios, texto, una instrucción por línea):
 *   Arduino -> PC:
 *     READY               El firmware arrancó y el sensor quedó listo.
 *     ERROR:VL53L0X        No se pudo inicializar el sensor de distancia.
 *     EVENT:DETECTADO      Se detectó una pieza; la banda ya se detuvo.
 *     EVENT:TIMEOUT         Se agotó el tiempo esperando RESULTADO:...;
 *                           la banda se reanudó sola, sin clasificar.
 *   PC -> Arduino:
 *     LUZ:<0-255>          Intensidad de la luz.
 *     RESULTADO:APROBADO   Clasificación de la pieza detenida: mueve el
 *     RESULTADO:DEFECTUOSO servo hacia ese lado, y reanuda la banda.
 *
 * Ver infrastructure/arduino_controller.py (interfaz Python) y el
 * README.md (sección "Conectar el Arduino con la interfaz") para el
 * lado de la PC.
 */

#include <Wire.h>
#include <VL53L0X.h>
#include <Servo.h>

// ==================== PINES ====================
const uint8_t PIN_ENA = 5;    // PWM de velocidad del motor (antes pin 9)
const uint8_t PIN_IN1 = 7;    // dirección del motor
const uint8_t PIN_IN2 = 8;    // dirección del motor
const uint8_t PIN_LUZ = 6;    // PWM de la luz
const uint8_t PIN_SERVO = 9;  // señal del servo clasificador

// ==================== BANDA (motor) ====================
const uint8_t VELOCIDAD_BANDA = 200;  // 0-255, velocidad de crucero de la banda

// ==================== SENSOR DE DISTANCIA ====================
VL53L0X sensor;
const uint16_t DISTANCIA_DELTA_MM = 30;     // cuánto debe achicarse la distancia
                                             // (algo más cerca del sensor) para
                                             // considerar que llegó una pieza
const uint8_t MUESTRAS_CALIBRACION = 10;    // lecturas usadas para fijar la
                                             // distancia "banda vacía" al arrancar
uint16_t distanciaBase = 0;                 // distancia de referencia (sin pieza)
const unsigned long BLACKOUT_REANUDAR_MS = 1500;  // tras reanudar la banda, ignora
                                                    // el sensor un rato para que la
                                                    // misma pieza no se re-detecte
                                                    // antes de alejarse

// ==================== SERVO CLASIFICADOR ====================
Servo servoClasificador;
const int ANGULO_NEUTRO = 90;       // reposo, en línea con la banda
const int ANGULO_APROBADO = 45;     // hacia el contenedor de piezas buenas
const int ANGULO_DEFECTUOSO = 135;  // hacia el contenedor de piezas defectuosas
const unsigned long SERVO_ESPERA_MS = 700;  // tiempo que se mantiene desviado
                                             // antes de volver al centro

// ==================== ESTADO / SEGURIDAD ====================
enum EstadoBanda { BANDA_CORRIENDO, BANDA_ESPERANDO_CLASIFICACION };
EstadoBanda estado = BANDA_CORRIENDO;

unsigned long detectadoEnMs = 0;
const unsigned long SAFETY_TIMEOUT_MS = 15000;  // si la PC no contesta en este
                                                  // tiempo, se reanuda sola
unsigned long ignorarSensorHastaMs = 0;

String lineaSerial;  // buffer para armar cada línea entrante por serie

// =================================================================
// SETUP
// =================================================================
void setup() {
  Serial.begin(115200);
  lineaSerial.reserve(32);

  pinMode(PIN_ENA, OUTPUT);
  pinMode(PIN_IN1, OUTPUT);
  pinMode(PIN_IN2, OUTPUT);
  pinMode(PIN_LUZ, OUTPUT);

  servoClasificador.attach(PIN_SERVO);
  servoClasificador.write(ANGULO_NEUTRO);

  analogWrite(PIN_LUZ, 255);  // luz al máximo por defecto al arrancar

  Wire.begin();
  sensor.setTimeout(500);
  if (!sensor.init()) {
    // Sin sensor no hay forma segura de operar la banda: se avisa a la
    // PC y se queda aquí (con la banda detenida) en vez de arrancar a
    // ciegas.
    Serial.println("ERROR:VL53L0X");
    while (true) {
      delay(1000);
      Serial.println("ERROR:VL53L0X");
    }
  }
  sensor.startContinuous();

  distanciaBase = calibrarDistanciaBase();
  encenderBanda();
  Serial.println("READY");
}

// =================================================================
// LOOP PRINCIPAL
// =================================================================
void loop() {
  procesarSerialEntrante();

  switch (estado) {
    case BANDA_CORRIENDO:
      revisarSensorDistancia();
      break;

    case BANDA_ESPERANDO_CLASIFICACION:
      if (millis() - detectadoEnMs > SAFETY_TIMEOUT_MS) {
        // La PC no contestó a tiempo (cámara caída, pieza no
        // reconocida, etc.): se reanuda sola para no trabar la línea.
        Serial.println("EVENT:TIMEOUT");
        encenderBanda();
        estado = BANDA_CORRIENDO;
      }
      break;
  }
}

// =================================================================
// SENSOR: calibración inicial y detección de piezas
// =================================================================
uint16_t calibrarDistanciaBase() {
  uint32_t suma = 0;
  uint8_t validas = 0;
  for (uint8_t i = 0; i < MUESTRAS_CALIBRACION; i++) {
    uint16_t d = sensor.readRangeContinuousMillimeters();
    if (!sensor.timeoutOccurred() && d > 0 && d < 8190) {
      suma += d;
      validas++;
    }
    delay(30);
  }
  return validas > 0 ? (uint16_t)(suma / validas) : 0;
}

void revisarSensorDistancia() {
  if (millis() < ignorarSensorHastaMs) {
    return;  // periodo de "blackout" tras reanudar la banda
  }

  uint16_t distancia = sensor.readRangeContinuousMillimeters();
  if (sensor.timeoutOccurred()) {
    return;  // lectura no confiable, se ignora este ciclo
  }

  // La pieza pasa MÁS CERCA del sensor que la banda vacía -> la
  // distancia medida se achica respecto a la base calibrada.
  if (distanciaBase > 0 && (distanciaBase - (long)distancia) >= DISTANCIA_DELTA_MM) {
    apagarBanda();
    estado = BANDA_ESPERANDO_CLASIFICACION;
    detectadoEnMs = millis();
    Serial.println("EVENT:DETECTADO");
  }
}

// =================================================================
// SERIE: instrucciones que llegan desde la interfaz en Python
// =================================================================
void procesarSerialEntrante() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n') {
      lineaSerial.trim();
      if (lineaSerial.length() > 0) {
        procesarComando(lineaSerial);
      }
      lineaSerial = "";
    } else if (c != '\r') {
      lineaSerial += c;
    }
  }
}

void procesarComando(const String &linea) {
  if (linea.startsWith("LUZ:")) {
    int valor = linea.substring(4).toInt();
    valor = constrain(valor, 0, 255);
    analogWrite(PIN_LUZ, valor);
  } else if (linea == "RESULTADO:APROBADO") {
    clasificarYReanudar(ANGULO_APROBADO);
  } else if (linea == "RESULTADO:DEFECTUOSO") {
    clasificarYReanudar(ANGULO_DEFECTUOSO);
  } else if (linea == "PING") {
    Serial.println("PONG");
  }
}

// =================================================================
// CLASIFICACIÓN: mueve el servo hacia el lado que corresponda,
// vuelve al centro y reanuda la banda.
// =================================================================
void clasificarYReanudar(int angulo) {
  if (estado != BANDA_ESPERANDO_CLASIFICACION) {
    return;  // ignora resultados fuera de tiempo (ya se reanudó sola)
  }
  servoClasificador.write(angulo);
  delay(SERVO_ESPERA_MS);
  servoClasificador.write(ANGULO_NEUTRO);

  encenderBanda();
  estado = BANDA_CORRIENDO;
}

// =================================================================
// MOTOR DE LA BANDA (driver L298N)
// =================================================================
void encenderBanda() {
  digitalWrite(PIN_IN1, HIGH);
  digitalWrite(PIN_IN2, LOW);
  analogWrite(PIN_ENA, VELOCIDAD_BANDA);
  ignorarSensorHastaMs = millis() + BLACKOUT_REANUDAR_MS;
}

void apagarBanda() {
  analogWrite(PIN_ENA, 0);
  digitalWrite(PIN_IN1, LOW);
  digitalWrite(PIN_IN2, LOW);
}
