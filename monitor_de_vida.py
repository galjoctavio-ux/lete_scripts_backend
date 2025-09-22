# -----------------------------------------------------
# Script Monitor de Vida para Dispositivos LETE (Versión Final Corregida)
# -----------------------------------------------------

# --- 1. Importar las librerías necesarias ---
import psycopg2
from influxdb_client_3 import InfluxDBClient3
import requests
import pandas as pd
import certifi
import os
import json
from datetime import datetime, timedelta, timezone

# --- 2. FORZAR LA RUTA DE CERTIFICADOS SSL ---
os.environ['GRPC_DEFAULT_SSL_ROOTS_FILE_PATH'] = certifi.where()

# --- 3. CONFIGURACIÓN ---
INFLUX_URL = "https://us-east-1-1.aws.cloud2.influxdata.com"
INFLUX_TOKEN = "Ngu_66P3bgxtwqXhhBWpazpexNFfKFL9FfWkokdSG2T8DupYvuq8GnbQ0RU1XrKevbZYYuIDe4sQMoPeqnDTlA=="
INFLUX_ORG = "LETE"
INFLUX_BUCKET = "mediciones_energia"

DB_HOST = "db.evpcxzhdkjwbnpwnzyme.supabase.co"
DB_USER = "postgres"
DB_PASS = "kx&BKz7NTtnk5uy"
DB_NAME = "postgres"

TWILIO_ACCOUNT_SID = "AC3fb0bec1c75c0a1815aec82212e429b4"
TWILIO_AUTH_TOKEN = "51f619ae2e933355eeb3e4c5bbecfc3c"
TWILIO_FROM_NUMBER = "whatsapp:+14155238886"
TWILIO_URL = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"

# --- NÚMERO DEL ADMINISTRADOR ---
NUMERO_ADMIN = "+5213310043159"

# --- IDs DE LAS PLANTILLAS DE MENSAJE (TEMPLATES) ---
TPL_DISPOSITIVO_DESCONECTADO = "HX5da15200caf47accc09f25f7467604e9"
TPL_ALERTA_ADMIN = "HX66c1cf3936bb656c41ccf5d927748eaa" # <-- RECUERDA PEGAR EL SID DE TU PLANTILLA DE ADMIN

# --- Lógica de Negocio para esta alerta ---
UMBRAL_DESCONEXION_MINUTOS = 90

# --- 4. FUNCIONES ---

def obtener_clientes():
    """Se conecta a la base de datos de clientes y devuelve una lista de todos."""
    try:
        conn = psycopg2.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, dbname=DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT device_id, telefono_whatsapp, nombre FROM clientes")
        lista_clientes = cursor.fetchall()
        cursor.close()
        conn.close()
        print(f"✅ Se encontraron {len(lista_clientes)} clientes para monitorear.")
        return lista_clientes
    except Exception as e:
        print(f"❌ ERROR al conectar con la base de datos de clientes: {e}")
        return []

def enviar_alerta_whatsapp(telefono_destino, content_sid, content_variables):
    """Envía un mensaje usando una Plantilla de WhatsApp."""
    if not telefono_destino:
        print("⚠️  Teléfono vacío. No se envía alerta.")
        return
    payload = {
        "ContentSid": content_sid,
        "ContentVariables": json.dumps(content_variables),
        "From": TWILIO_FROM_NUMBER,
        "To": f"whatsapp:{telefono_destino}"
    }
    print(f"\nEnviando WhatsApp (Plantilla {content_sid}) a: {telefono_destino}...")
    try:
        response = requests.post(TWILIO_URL, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), data=payload)
        if response.status_code == 201:
            print(f"✔️ Alerta enviada exitosamente.")
        else:
            print(f"⚠️  Error al enviar alerta. Código: {response.status_code}, Respuesta: {response.text}")
    except Exception as e:
        print(f"❌ ERROR fatal al intentar enviar WhatsApp: {e}")

def verificar_ultimo_reporte(client_influx, cliente):
    """
    Consulta la hora del último dato recibido de un dispositivo y la compara con la hora actual.
    """
    device_id, telefono, nombre = cliente
    print(f"-> Verificando último reporte de: {nombre} ({device_id})")
    device_id_esc = str(device_id).replace("'", "''")

    query = f"""
        SELECT
            MAX(time) AS ultimo_reporte
        FROM
            "energia"
        WHERE
            "deviceId" = '{device_id_esc}'
    """
    try:
        tabla = client_influx.query(query=query, language="sql")
        df = tabla.to_pandas()
        
        if not df.empty and pd.notna(df["ultimo_reporte"].iloc[0]):
            ultimo_reporte = df["ultimo_reporte"].iloc[0]

            # --------------------------------------------------------------------------
            # CORRECCIÓN DEFINITIVA: Forzamos la zona horaria UTC en la fecha de la base de datos
            # y obtenemos la fecha actual también en UTC para asegurar la compatibilidad.
            ultimo_reporte_utc = ultimo_reporte.replace(tzinfo=timezone.utc)
            ahora_utc = datetime.now(timezone.utc)
            # --------------------------------------------------------------------------
            
            diferencia = ahora_utc - ultimo_reporte_utc
            minutos_desconectado = diferencia.total_seconds() / 60
            
            print(f"   Último reporte hace: {minutos_desconectado:.1f} minutos.")

            if minutos_desconectado > UMBRAL_DESCONEXION_MINUTOS:
                print(f"   ⚠️ ¡ALERTA! El dispositivo de {nombre} parece estar desconectado.")
                
                variables_cliente = {"1": nombre}
                enviar_alerta_whatsapp(telefono, TPL_DISPOSITIVO_DESCONECTADO, variables_cliente)
                
                variables_admin = {
                    "1": nombre,
                    "2": device_id,
                    "3": f"{minutos_desconectado:.0f}"
                }
                enviar_alerta_whatsapp(NUMERO_ADMIN, TPL_ALERTA_ADMIN, variables_admin)
        else:
            print(f"   No se encontraron datos para el dispositivo {device_id}. Posiblemente nunca ha reportado.")

    except Exception as e:
        print(f"❌ ERROR al consultar último reporte para {device_id}: {e}")

# --- 5. EJECUCIÓN PRINCIPAL ---
def main():
    print("=============================================")
    print(f"--- Iniciando MONITOR DE VIDA de Dispositivos ({datetime.now()}) ---")
    
    try:
        client_influx = InfluxDBClient3(host=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG, database=INFLUX_BUCKET)
        print("✅ Conexión con InfluxDB exitosa.")
    except Exception as e:
        print(f"❌ ERROR de conexión con InfluxDB. Abortando. Detalles: {e}")
        return

    clientes = obtener_clientes()
    
    if not clientes:
        print("No hay clientes para monitorear. Terminando script.")
        return

    for cliente in clientes:
        verificar_ultimo_reporte(client_influx, cliente)

    print("\n--- MONITOR DE VIDA de Dispositivos completado. ---")
    print("=============================================")

if __name__ == "__main__":
    main()