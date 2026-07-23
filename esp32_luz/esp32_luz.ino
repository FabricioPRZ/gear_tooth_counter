/*
 * ESP32 - Control de intensidad de luz (PWM) por WiFi.
 *
 * Basado en tu sketch original (que recibía el brillo por Serial); ahora lo
 * recibe por WiFi desde la interfaz de Python vía una petición HTTP simple,
 * para que el slider de la GUI pueda controlar la luz sin cable USB.
 *
 * Requiere las librerías "WiFi", "WebServer" y "ESPmDNS" (vienen incluidas
 * en el core de ESP32 para Arduino, no hay que instalar nada aparte).
 *
 * ANTES DE SUBIRLO:
 *   1) Completa WIFI_SSID y WIFI_PASSWORD abajo con los datos de tu red
 *      (debe ser la MISMA red donde está la ESP32-CAM y la PC con la app).
 *   2) Revisa que PWM_PIN sea el pin donde realmente conectaste el LED/MOSFET
 *      en TU placa ESP32 (el pin 9 del sketch original es de Arduino clásico,
 *      no existe igual en todas las ESP32 — cámbialo si es necesario).
 *
 * Una vez conectada a WiFi, queda accesible en la red como 'http://luz.local'
 * (iigual que la cámara en 'http://camara.local'), así la app de Python no
 * necesita saber su IP.
 *
 * Endpoints HTTP:
 *   GET /brillo?valor=0..255   -> fija el brillo y responde con el valor aplicado
 *   GET /estado                -> responde con el brillo actual
 */
#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>

const char* WIFI_SSID = "S25U";
const char* WIFI_PASSWORD = "123456790";

const byte PWM_PIN = 13;   // <-- confirma que coincide con tu cableado real

int brillo = 255;  // Valor inicial (0-255)

WebServer server(80);

void aplicarBrillo(int valor) {
  brillo = constrain(valor, 0, 255);
  analogWrite(PWM_PIN, brillo);
  Serial.print("Brillo: ");
  Serial.println(brillo);
}

void handleBrillo() {
  if (!server.hasArg("valor")) {
    server.send(400, "text/plain", "Falta el parametro 'valor' (0-255)");
    return;
  }
  aplicarBrillo(server.arg("valor").toInt());
  server.send(200, "text/plain", String(brillo));
}

void handleEstado() {
  server.send(200, "text/plain", String(brillo));
}

void setup() {
  pinMode(PWM_PIN, OUTPUT);
  Serial.begin(9600);
  aplicarBrillo(brillo);

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Conectando a WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(400);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("Conectado. IP: ");
  Serial.println(WiFi.localIP());

  if (MDNS.begin("luz")) {
    Serial.println("mDNS activo: ahora se puede usar http://luz.local");
  } else {
    Serial.println("No se pudo iniciar mDNS (usa la IP directamente en ese caso).");
  }

  server.on("/brillo", handleBrillo);
  server.on("/estado", handleEstado);
  server.begin();
  Serial.println("Servidor HTTP listo.");
}

void loop() {
  server.handleClient();
}
