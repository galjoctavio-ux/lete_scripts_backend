// ==========================================================================
// == FIRMWARE LETE - MONITOR DE ENERGÍA v4.3
// ==
// == MEJORAS:
// == - Calibración persistente guardada en SPIFFS.
// == - Página web de calibración.
// == - Contraseña de seguridad para la interfaz web de diagnóstico.
// =========================================================================


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
#include "FS.h"
#include "SPIFFS.h"
#include <ArduinoJson.h>
#include "EmonLib.h"
#include "time.h"

// --- 2. CONFIGURACIÓN PRINCIPAL ---
const float FIRMWARE_VERSION = 4.3;
#define SERVICE_TYPE "1F"
const bool OLED_CONECTADA = false; // Ajusta según si tienes la pantalla conectada

// --- Contraseña para la Interfaz Web de Diagnóstico ---
const char* http_username = "admin";
const char* http_password = "123456788"; // <-- CAMBIA ESTA CONTRASEÑA

// --- URLs para Actualización Remota (OTA por HTTP) ---
const char* firmware_version_url = "https://raw.githubusercontent.com/galjoctavio-ux/lete_scripts_backend/main/firmware.version";

// Usamos %s como un comodín para el número de versión
const char* firmware_bin_url_template = "https://github.com/galjoctavio-ux/lete_scripts_backend/releases/download/%s/firmware.bin";

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
float voltage_cal = 265.0;
float current_cal_1 = 11.07;
float current_cal_2 = 11.71;

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
unsigned long last_update_check = 0;

// --- 5. FUNCIONES ---

void saveCalibration() {
    SPIFFS.remove("/calibracion.json");
    File file = SPIFFS.open("/calibracion.json", FILE_WRITE);
    if (!file) {
        Serial.println("Error al abrir archivo de calibracion para escritura");
        return;
    }
    StaticJsonDocument<256> doc;
    doc["voltage_cal"] = voltage_cal;
    doc["current_cal_1"] = current_cal_1;
    doc["current_cal_2"] = current_cal_2;
    if (serializeJson(doc, file) == 0) {
        Serial.println("Error al escribir en archivo de calibracion");
    } else {
        Serial.println("Calibracion guardada en SPIFFS.");
    }
    file.close();
}

void loadCalibration() {
    File file = SPIFFS.open("/calibracion.json", FILE_READ);
    if (!file || file.size() == 0) {
        Serial.println("No se encontro archivo de calibracion, usando valores por defecto.");
        saveCalibration(); // Guarda los valores por defecto la primera vez
        return;
    }
    StaticJsonDocument<256> doc;
    DeserializationError error = deserializeJson(doc, file);
    if (error) {
        Serial.println("Error al leer archivo de calibracion, usando valores por defecto.");
        return;
    }
    voltage_cal = doc["voltage_cal"] | voltage_cal;
    current_cal_1 = doc["current_cal_1"] | current_cal_1;
    current_cal_2 = doc["current_cal_2"] | current_cal_2;
    Serial.println("Calibracion cargada desde SPIFFS.");
    file.close();
}

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

void drawUpdateScreen(String text) {
  if (OLED_CONECTADA) {
    display.clearDisplay();
    display.setTextSize(1);
    display.setCursor(0, 0);
    display.println("--- ACTUALIZANDO ---");
    display.setTextSize(2);
    display.setCursor(10, 25);
    display.println(text);
    display.display();
  } else {
    Serial.println("--- ACTUALIZANDO ---");
    Serial.println(text);
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
  Serial.println("Buscando actualizaciones remotas...");
  if (OLED_CONECTADA) drawUpdateScreen("Buscando...");

  HTTPClient http;
  http.begin(firmware_version_url);
  int httpCode = http.GET();
  
  if (httpCode == HTTP_CODE_OK) {
    String version_str = http.getString();
    float new_version = version_str.toFloat(); // CORRECCIÓN: variable correcta es new_version
    Serial.printf("Version actual: %.2f, Version en servidor: %s\n", FIRMWARE_VERSION, version_str.c_str());

    if (new_version > FIRMWARE_VERSION) {
      Serial.println("Nueva version disponible. Actualizando...");
      
      char final_firmware_url[256];
      sprintf(final_firmware_url, firmware_bin_url_template, ("v" + version_str).c_str());

      Serial.print("URL de descarga final: ");
      Serial.println(final_firmware_url);

      if (OLED_CONECTADA) drawUpdateScreen("Descargando");
      
      HTTPClient httpUpdateClient;
      httpUpdateClient.begin(final_firmware_url); // CORRECCIÓN: Usar la URL final
      t_httpUpdate_return ret = httpUpdate.update(httpUpdateClient);

      switch (ret) {
        case HTTP_UPDATE_FAILED:
          Serial.printf("Actualizacion Fallida. Error (%d): %s\n", httpUpdate.getLastError(), httpUpdate.getLastErrorString().c_str());
          if (OLED_CONECTADA) drawUpdateScreen("Error!");
          delay(2000);
          break;
        case HTTP_UPDATE_OK:
          Serial.println("¡Actualizacion exitosa! Reiniciando...");
          break;
      }
    } else {
      Serial.println("El firmware ya esta actualizado.");
    }
  } else {
    Serial.printf("Error al verificar version. Codigo HTTP: %d\n", httpCode);
  }
  http.end();
}

void handleRoot() {
  if (!server.authenticate(http_username, http_password)) {
    return server.requestAuthentication();
  }
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
  html += "<h2>Acciones</h2>";
  html += "<p><a href='/update'>Buscar Actualizaciones de Firmware</a></p>";
  html += "<p><a href='/reset-wifi'>Borrar Credenciales Wi-Fi</a></p>";
  html += "</body></html>";
  server.send(200, "text/html", html);
}

void handleUpdate() {
  if (!server.authenticate(http_username, http_password)) {
    return server.requestAuthentication();
  }
    server.send(200, "text/plain", "OK. Buscando actualizaciones... Revisa el Monitor Serie.");
    delay(100);
    checkForHttpUpdate();
}

void handleResetWifi() {
  if (!server.authenticate(http_username, http_password)) {
    return server.requestAuthentication();
  }
  server.send(200, "text/plain", "OK. Credenciales Wi-Fi borradas. El dispositivo se reiniciara en Modo Configuracion.");
  delay(1000);
  WiFiManager wm;
  wm.resetSettings();
  ESP.restart();
}

void handleCalibration() {
    if (!server.authenticate(http_username, http_password)) {
        return server.requestAuthentication();
    }
    if (server.hasArg("voltage") && server.hasArg("current1") && server.hasArg("current2")) {
        voltage_cal = server.arg("voltage").toFloat();
        current_cal_1 = server.arg("current1").toFloat();
        current_cal_2 = server.arg("current2").toFloat();
        saveCalibration();
        server.send(200, "text/plain", "OK. Calibracion guardada. Reinicia el dispositivo para aplicar.");
    } else {
        String html = "<html><head><title>Calibracion LETE</title></head><body>";
        html += "<h1>Calibracion del Dispositivo</h1>";
        html += "<form action='/calibracion' method='POST'>";
        html += "Voltaje (V_CAL): <input type='text' name='voltage' value='" + String(voltage_cal) + "'><br>";
        html += "Corriente 1 (I_CAL1): <input type='text' name='current1' value='" + String(current_cal_1) + "'><br>";
        html += "Corriente 2 (I_CAL2): <input type='text' name='current2' value='" + String(current_cal_2) + "'><br>";
        html += "<input type='submit' value='Guardar'>";
        html += "</form></body></html>";
        server.send(200, "text/html", html);
    }
}

// --- 6. FUNCIÓN DE SETUP ---
void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_PIN, INPUT_PULLUP);

  setupOLED();

  // INICIA EL SISTEMA DE ARCHIVOS Y CARGA LA CALIBRACIÓN
  SPIFFS.begin(true);
  loadCalibration();

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

  emon1.voltage(VOLTAGE_SENSOR_PIN, voltage_cal, 1.7);
  emon1.current(CURRENT_SENSOR_PIN_1, current_cal_1);
  emon2.current(CURRENT_SENSOR_PIN_2, current_cal_2);

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
  server.on("/calibracion", HTTP_GET, handleCalibration);  // Página para mostrar el formulario
  server.on("/calibracion", HTTP_POST, handleCalibration); // Ruta para recibir los datos
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

  // Verificación periódica de actualizaciones remotas
  if (WiFi.status() == WL_CONNECTED && millis() - last_update_check > (4 * 3600 * 1000)) { // Cada 4 horas
      last_update_check = millis();
      checkForHttpUpdate();
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