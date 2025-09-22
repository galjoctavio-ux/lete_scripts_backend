// ==========================================================================
// == FIRMWARE LETE - MONITOR DE ENERGÍA v4.1 (Optimizado)
// ==
// == MEJORAS:
// == - Gestión de memoria de HTTPClient optimizada para evitar errores de conexión.
// == - Aumentado el tiempo de espera (timeout) para conexiones seguras.
// == - Telemetría de memoria RAM en Monitor Serie.
// ==========================================================================

// --- 1. LIBRERÍAS ---
#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <ArduinoOTA.h>
#include <DNSServer.h>
#include <WiFiManager.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <HTTPClient.h>
#include <HTTPUpdate.h>
#include "EmonLib.h"
#include "time.h"

// --- 2. CONFIGURACIÓN PRINCIPAL ---
const float FIRMWARE_VERSION = 4.1;
#define SERVICE_TYPE "1F"
const bool OLED_CONECTADA = false; // Ajusta según si tienes la pantalla conectada

// --- URLs para Actualización Remota (OTA por HTTP) ---
const char* firmware_version_url = "https://raw.githubusercontent.com/tu-usuario/tu-repo/main/firmware.version";
const char* firmware_bin_url = "https://raw.githubusercontent.com/tu-usuario/tu-repo/main/firmware.bin";

// --- Configuración de InfluxDB ---
#define INFLUXDB_URL "https://us-east-1-1.aws.cloud2.influxdata.com/api/v2/write?org=LETE&bucket=mediciones_energia&precision=s"
#define INFLUXDB_TOKEN "Ngu_66P3bgxtwqXhhBWpazpexNFfKFL9FfWkokdSG2T8DupYvuq8GnbQ0RU1XrKevbZYYuIDe4sQMoPeqnDTlA=="
#define INFLUXDB_MEASUREMENT_STATE "energia_estado"
const char* ntpServer = "pool.ntp.org";

// --- 3. CONFIGURACIÓN DE PINES ---
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
#define I2C_SDA 21
#define I2C_SCL 22
#define BUTTON_PIN 0

const int VOLTAGE_SENSOR_PIN = 34;
const int CURRENT_SENSOR_PIN_1 = 35; // Fase
const int CURRENT_SENSOR_PIN_2 = 32; // Neutro

// --- Calibración de Sensores ---
const float VOLTAGE_CAL = 265.0;
const float CURRENT_CAL_1 = 11.07;
const float CURRENT_CAL_2 = 11.71;

// --- 4. OBJETOS Y VARIABLES GLOBALES ---
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
WebServer server(80);
EnergyMonitor emon1, emon2;

// Variables para lecturas y estado
float latest_vrms = 0.0;
float latest_irms1 = 0.0;
float latest_irms2 = 0.0;
float latest_power = 0.0;
float latest_leakage = 0.0;
bool server_status = false;
int screen_mode = 0;
long last_button_press = 0;
unsigned long last_measurement_time = 0;

// --- 5. FUNCIONES ---

void setupOLED() {
  if (OLED_CONECTADA) {
    Wire.begin(I2C_SDA, I2C_SCL);
    if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
      Serial.println(F("Fallo al iniciar SSD1306"));
    }
    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);
  }
}

void drawBootScreen() {
  if (OLED_CONECTADA) {
    display.clearDisplay();
    display.setTextSize(2);
    display.setCursor(0, 0);
    display.println("Luz en tu");
    display.println("Espacio");
    display.setTextSize(1);
    display.setCursor(0, 40);
    display.print("Firmware: v");
    display.println(FIRMWARE_VERSION, 1);
    display.print("Servicio: ");
    display.println(SERVICE_TYPE);
    display.display();
  } else {
    Serial.println("\n========================");
    Serial.println("Luz en tu Espacio");
    Serial.print("Firmware: v"); Serial.println(FIRMWARE_VERSION, 1);
    Serial.print("Servicio: "); Serial.println(SERVICE_TYPE);
    Serial.println("========================");
  }
  delay(3000);
}

void drawConfigScreen(const char* apName) {
  if (OLED_CONECTADA) {
    display.clearDisplay();
    display.setTextSize(1);
    display.setCursor(0, 0);
    display.println("--- MODO CONFIGURACION ---");
    display.println("\nConectate a la red Wi-Fi:");
    display.setTextSize(2);
    display.println(apName);
    display.setTextSize(1);
    display.println("\n(192.168.4.1 en tu\nnavegador si no se abre\nautomaticamente)");
    display.display();
  } else {
    Serial.println("\n--- MODO CONFIGURACION ---");
    Serial.print("Conectate a la red Wi-Fi: ");
    Serial.println(apName);
    Serial.println("Abre 192.168.4.1 en tu navegador.");
  }
}

const char* getWifiIcon(int rssi) {
    if (rssi > -70) return "[|||]";
    if (rssi > -80) return "[||-]";
    if (rssi > -90) return "[|--]";
    return "[---]";
}

void drawMainScreen() {
    if (!OLED_CONECTADA) return;
    display.clearDisplay();
    display.setTextSize(2);
    display.setCursor(0, 0);
    display.printf("%.1f V", latest_vrms);
    display.setCursor(0, 20);
    display.printf("%.2f A", latest_irms1);
    display.setTextSize(1);
    display.setCursor(0, 40);
    display.printf("%.0f W", latest_power);
    display.setCursor(0, 55);
    display.printf("WiFi:%s", getWifiIcon(WiFi.RSSI()));
    display.setCursor(70, 55);
    display.print(server_status ? "Nube:OK" : "Nube:--");
    display.display();
}

void drawConnectivityScreen() {
    if (!OLED_CONECTADA) return;
    display.clearDisplay();
    display.setTextSize(1);
    display.setCursor(0, 0);
    display.println("-- CONECTIVIDAD --");
    display.setCursor(0, 15);
    display.printf("Red: %s\n", WiFi.SSID().c_str());
    display.printf("Senal: %d dBm\n", WiFi.RSSI());
    display.printf("IP: %s\n", WiFi.localIP().toString().c_str());
    display.display();
}

void drawDiagnosticsScreen() {
    if (!OLED_CONECTADA) return;
    long uptime_seconds = millis() / 1000;
    int days = uptime_seconds / 86400;
    int hours = (uptime_seconds % 86400) / 3600;
    int minutes = (uptime_seconds % 3600) / 60;

    display.clearDisplay();
    display.setTextSize(1);
    display.setCursor(0, 0);
    display.println("--- DIAGNOSTICO ---");
    display.setCursor(0, 15);
    display.printf("Uptime: %dd %dh %dm\n", days, hours, minutes);
    display.printf("Memoria Libre: %d KB\n", ESP.getFreeHeap() / 1024);
    display.display();
}

void checkForHttpUpdate() {
  // ... (Esta función se puede rellenar si se necesita OTA remoto)
}

void handleRoot() {
  char uptime_str[20];
  long uptime_seconds = millis() / 1000;
  sprintf(uptime_str, "%dd %dh %dm", uptime_seconds / 86400, (uptime_seconds % 86400) / 3600, (uptime_seconds % 3600) / 60);

  String html = "<html><head><title>Monitor LETE</title><meta http-equiv='refresh' content='5'><meta name='viewport' content='width=device-width, initial-scale=1'><style>body{font-family:sans-serif;}</style></head><body>";
  html += "<h1>Monitor LETE v" + String(FIRMWARE_VERSION, 1) + "</h1>";
  html += "<h2>Estado Principal</h2>";
  html += "<p><b>Voltaje:</b> " + String(latest_vrms, 1) + " V</p>";
  html += "<p><b>Corriente:</b> " + String(latest_irms1, 2) + " A</p>";
  html += "<p><b>Potencia:</b> " + String(latest_power, 0) + " W</p>";
  html += "<p><b>Fuga:</b> " + String(latest_leakage, 3) + " A</p>";
  html += "<h2>Conectividad</h2>";
  html += "<p><b>Red:</b> " + WiFi.SSID() + " (" + String(WiFi.RSSI()) + " dBm)</p>";
  html += "<p><b>IP:</b> " + WiFi.localIP().toString() + "</p>";
  html += "<p><b>Nube:</b> " + String(server_status ? "OK" : "Error") + "</p>";
  html += "<h2>Diagnostico del Sistema</h2>";
  html += "<p><b>Uptime:</b> " + String(uptime_str) + "</p>";
  html += "<p><b>Memoria Libre:</b> " + String(ESP.getFreeHeap() / 1024) + " KB</p>";
  html += "</body></html>";
  server.send(200, "text/html", html);
}

void handleUpdate() {
    server.send(200, "text/plain", "OK. Buscando actualizaciones... Revisa el Monitor Serie.");
    delay(100);
    checkForHttpUpdate();
}

void handleResetWifi() {
  server.send(200, "text/plain", "OK. Credenciales Wi-Fi borradas. El dispositivo se reiniciara en Modo Configuracion.");
  delay(1000);
  WiFiManager wm;
  wm.resetSettings();
  ESP.restart();
}

// --- 6. FUNCIÓN DE SETUP ---
void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_PIN, INPUT_PULLUP);

  setupOLED();

  Serial.println("Mantenga presionado el boton BOOT por 5s para borrar WiFi...");
  if (OLED_CONECTADA) {
      display.clearDisplay();
      display.setTextSize(1);
      display.setCursor(0,0);
      display.println("Mantenga presionado\nBOOT por 5s para\nborrar WiFi...");
      display.display();
  }
  long pressStartTime = millis();
  while(digitalRead(BUTTON_PIN) == LOW){
    if(millis() - pressStartTime > 5000){
      WiFiManager wm;
      wm.resetSettings();
      Serial.println("Credenciales Wi-Fi borradas! Reiniciando...");
      // --- CORRECCIÓN ---
      if(OLED_CONECTADA) {
        display.clearDisplay();
        display.setTextSize(2);
        display.setCursor(0, 25);
        display.println("WiFi Borrado");
        display.display();
      }
      delay(2000);
      ESP.restart();
    }
  }

  drawBootScreen();

  emon1.voltage(VOLTAGE_SENSOR_PIN, VOLTAGE_CAL, 1.7);
  emon1.current(CURRENT_SENSOR_PIN_1, CURRENT_CAL_1);
  emon2.current(CURRENT_SENSOR_PIN_2, CURRENT_CAL_2);

  WiFiManager wm;
  String apName = "LETE-Monitor-Config";
  wm.setAPCallback([&](WiFiManager* myWiFiManager) {
    if (OLED_CONECTADA) drawConfigScreen(apName.c_str());
  });
  if (!wm.autoConnect(apName.c_str())) {
    ESP.restart();
  }
  
  Serial.println("\nConectado a la red Wi-Fi!");
  Serial.println("Esperando 2 segundos para estabilizar la conexion...");
  delay(2000);

  configTime(0, 0, ntpServer);
  
  ArduinoOTA.setHostname("lete-monitor");
  ArduinoOTA.setPassword("Pegaso18");
  ArduinoOTA.begin();

  server.on("/", handleRoot);
  server.on("/update", handleUpdate);
  server.on("/reset-wifi", handleResetWifi);
  server.begin();
}

// --- 7. FUNCIÓN DE LOOP PRINCIPAL ---
void loop() {
  ArduinoOTA.handle();
  server.handleClient();

  if (OLED_CONECTADA) {
    if (digitalRead(BUTTON_PIN) == LOW && (millis() - last_button_press > 500)) {
      last_button_press = millis();
      screen_mode = (screen_mode + 1) % 3;
    }
  }

  // Bucle principal de medición y envío cada 2 segundos
  if (millis() - last_measurement_time > 2000) {
    last_measurement_time = millis();

    // 1. Realizar mediciones
    emon1.calcVI(20, 2000);
    emon2.calcIrms(1480);
    
    latest_vrms = emon1.Vrms;
    latest_irms1 = emon1.Irms;
    latest_power = emon1.realPower;
    latest_irms2 = emon2.Irms;
    latest_leakage = abs(latest_irms1 - latest_irms2);

    // 2. Imprimir telemetría en Monitor Serie
    Serial.println("\n--- Telemetria de Estado ---");
    Serial.printf("Voltaje: %.1f V | Potencia: %.0f W\n", latest_vrms, latest_power);
    Serial.printf("C. Fase: %.3f A | C. Neutro: %.3f A | Fuga: %.3f A\n", latest_irms1, latest_irms2, latest_leakage);
    
    // 3. Enviar datos a InfluxDB
    if (WiFi.status() == WL_CONNECTED) {
      HTTPClient http;
      
      http.begin(INFLUXDB_URL);
      http.addHeader("Authorization", "Token " + String(INFLUXDB_TOKEN));
      http.addHeader("Content-Type", "text/plain");
      http.setTimeout(10000);

      String lineProtocol = String(INFLUXDB_MEASUREMENT_STATE) +
                          ",deviceId=" + WiFi.macAddress() +
                          " voltaje=" + String(latest_vrms) +
                          ",corriente_fase=" + String(latest_irms1) +
                          ",corriente_neutro=" + String(latest_irms2) +
                          ",fuga_corriente=" + String(latest_leakage) +
                          ",potencia_activa=" + String(latest_power);
      
      Serial.printf("Memoria Libre antes de enviar: %d bytes\n", ESP.getFreeHeap());
      
      int httpCode = http.POST(lineProtocol);
      server_status = (httpCode == 204);

      if(server_status) {
        Serial.println("Datos enviados a InfluxDB exitosamente.");
      } else {
        Serial.printf("Error al enviar estado a InfluxDB: %d\n", httpCode);
      }

      http.end();
    }
  }

  // 4. Actualizar la pantalla OLED
  if (OLED_CONECTADA) {
    switch (screen_mode) {
      case 0: drawMainScreen(); break;
      case 1: drawConnectivityScreen(); break;
      case 2: drawDiagnosticsScreen(); break;
    }
  }
}