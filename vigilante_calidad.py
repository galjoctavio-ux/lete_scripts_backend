# -----------------------------------------------------
# Script Vigilante de Calidad de Energía para LETE
# Se ejecuta cada hora para alertas de alta frecuencia
# -----------------------------------------------------

# --- 1. Importar las librerías necesarias ---
import psycopg2
from influxdb_client_3 import InfluxDBClient3
import requests
import pandas as pd
import certifi
import os
import json
from datetime import date, datetime, timedelta

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

# --- IDs DE LAS PLANTILLAS DE MENSAJE (TEMPLATES) ---
TPL_PICOS_VOLTAJE = "HX2d8c5bf1effb2661e47811accebad95e"
TPL_BAJO_VOLTAJE = "HXf19921dcb05408f121e6e2e5957f2468"
TPL_FUGA_CORRIENTE = "HX9c335ad113116a6a9ec2281865af8516"
TPL_CAMBIO_NIVEL = "HX9c77f01246166e09d17943cc16a91bfd"
TPL_DAC = "HX00ca440d1495dd78ab371783a8f0e4ed"
TPL_CONSUMO_NOCTURNO = "HXe28fff6b46d7470b22b4568b61332ebe"

# --- Lógica de Negocio para esta alerta ---
UMBRAL_VOLTAJE_ALTO = 139.7
UMBRAL_VOLTAJE_BAJO = 114.3
CANTIDAD_PICOS_PARA_ALERTA = 2
UMBRAL_FUGA_CORRIENTE = 0.5 # Amperes
UMBRAL_CONSUMO_NOCTURNO = 3.0 # Amperes
LIMITE_TARIFA_BASICA = 150 # kWh (Primer escalón bimestral es 300, pero notificamos al pasar el equivalente a un mes)
LIMITE_DAC = 500 # kWh

# --- 4. FUNCIONES ---

def obtener_clientes():
    """Se conecta a la base de datos de clientes y devuelve una lista de todos los clientes."""
    try:
        conn = psycopg2.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, dbname=DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT device_id, telefono_whatsapp, nombre, dia_de_corte, tipo_tarifa, ciclo_bimestral, notificacion_nivel_tarifa, notificacion_dac FROM clientes")
        lista_clientes = cursor.fetchall()
        cursor.close()
        conn.close()
        print(f"✅ Se encontraron {len(lista_clientes)} clientes en la base de datos.")
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

def marcar_notificacion_enviada(device_id, tipo_notificacion):
    """Actualiza la bandera de notificación en Supabase para no enviar mensajes repetidos."""
    columna_a_actualizar = f"notificacion_{tipo_notificacion}"
    print(f"Actualizando bandera '{columna_a_actualizar}' para {device_id}...")
    try:
        conn = psycopg2.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, dbname=DB_NAME)
        cursor = conn.cursor()
        sql_update_query = f"UPDATE clientes SET {columna_a_actualizar} = true WHERE device_id = %s"
        cursor.execute(sql_update_query, (device_id,))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"❌ ERROR al actualizar bandera de notificación para {device_id}: {e}")

def calcular_fechas_corte(hoy, dia_de_corte, ciclo_bimestral):
    """Calcula la fecha de corte más reciente y la próxima."""
    mes_inicio_ciclo = 2 if ciclo_bimestral == 'par' else 1
    ultima_fecha = None
    for i in range(6):
        mes_candidato = hoy.month - i
        ano_candidato = hoy.year
        if mes_candidato <= 0:
            mes_candidato += 12
            ano_candidato -= 1
        if (mes_candidato - mes_inicio_ciclo) % 2 == 0:
            fecha_candidata = date(ano_candidato, mes_candidato, dia_de_corte)
            if fecha_candidata <= hoy:
                ultima_fecha = fecha_candidata
                break
    if not ultima_fecha:
        return None, None
    proximo_mes = ultima_fecha.month + 2
    proximo_ano = ultima_fecha.year
    if proximo_mes > 12:
        proximo_mes -= 12
        proximo_ano += 1
    proxima_fecha = date(proximo_ano, proximo_mes, dia_de_corte)
    return ultima_fecha, proxima_fecha

def obtener_consumo_kwh_en_rango(client_influx, device_id, fecha_inicio, fecha_fin):
    """Obtiene el total de kWh consumidos en un rango de fechas específico."""
    device_id_esc = str(device_id).replace("'", "''")
    fecha_inicio_str = fecha_inicio.strftime('%Y-%m-%d')
    fecha_fin_siguiente = fecha_fin + timedelta(days=1)
    fecha_fin_str = fecha_fin_siguiente.strftime('%Y-%m-%d')
    query = f"""
        WITH intervals AS (
          SELECT
            time,
            potencia_activa,
            LEAD(time) OVER (ORDER BY time) as next_time,
            LEAD(potencia_activa) OVER (ORDER BY time) as next_potencia
          FROM "energia"
          WHERE
            "deviceId" = '{device_id_esc}' AND
            time >= '{fecha_inicio_str}T00:00:00Z' AND time < '{fecha_fin_str}T00:00:00Z'
        )
        SELECT SUM((potencia_activa + next_potencia) / 2.0 * EXTRACT(EPOCH FROM (next_time - time))) / 3600000.0 AS kwh_total
        FROM intervals
        WHERE next_time IS NOT NULL;
    """
    try:
        tabla = client_influx.query(query=query, language="sql")
        df = tabla.to_pandas()
        if not df.empty and pd.notna(df["kwh_total"].iloc[0]):
            return float(df["kwh_total"].iloc[0])
        else:
            return 0.0
    except Exception as e:
        print(f"❌ ERROR al consultar kWh en rango para {device_id} ({fecha_inicio_str} a {fecha_fin_str}): {e}")
        return None

# --- Funciones de Verificación de Alertas ---

def verificar_voltaje(client_influx, cliente):
    """Revisa picos de alto y bajo voltaje en las última hora."""
    print("-> Verificando voltaje...")
    device_id_esc = str(cliente['device_id']).replace("'", "''")
    query_altos = f"""SELECT COUNT(voltaje) AS numero_de_picos FROM "energia" WHERE "deviceId" = '{device_id_esc}' AND time >= now() - interval '1 hours' AND voltaje > {UMBRAL_VOLTAJE_ALTO}"""
    query_bajos = f"""SELECT COUNT(voltaje) AS numero_de_picos FROM "energia" WHERE "deviceId" = '{device_id_esc}' AND time >= now() - interval '1 hours' AND voltaje < {UMBRAL_VOLTAJE_BAJO}"""
    try:
        df_altos = client_influx.query(query=query_altos, language="sql").to_pandas()
        if not df_altos.empty and pd.notna(df_altos["numero_de_picos"].iloc[0]):
            picos_altos = int(df_altos["numero_de_picos"].iloc[0])
            if picos_altos >= CANTIDAD_PICOS_PARA_ALERTA:
                variables = {"1": cliente['nombre'], "2": str(picos_altos)}
                enviar_alerta_whatsapp(cliente['telefono'], TPL_PICOS_VOLTAJE, variables)
        df_bajos = client_influx.query(query=query_bajos, language="sql").to_pandas()
        if not df_bajos.empty and pd.notna(df_bajos["numero_de_picos"].iloc[0]):
            picos_bajos = int(df_bajos["numero_de_picos"].iloc[0])
            if picos_bajos >= CANTIDAD_PICOS_PARA_ALERTA:
                variables = {"1": cliente['nombre']}
                enviar_alerta_whatsapp(cliente['telefono'], TPL_BAJO_VOLTAJE, variables)
    except Exception as e:
        print(f"❌ ERROR al consultar picos de voltaje para {cliente['device_id']}: {e}")

def verificar_fuga_corriente(client_influx, cliente):
    """Revisa el promedio de fuga de corriente en la última hora."""
    print("-> Verificando fuga de corriente...")
    device_id_esc = str(cliente['device_id']).replace("'", "''")
    query = f"""SELECT MEAN(fuga_corriente) AS promedio_fuga FROM "energia" WHERE "deviceId" = '{device_id_esc}' AND time >= now() - interval '1 hour'"""
    try:
        df = client_influx.query(query=query, language="sql").to_pandas()
        if not df.empty and pd.notna(df["promedio_fuga"].iloc[0]):
            fuga_promedio = float(df["promedio_fuga"].iloc[0])
            if fuga_promedio > UMBRAL_FUGA_CORRIENTE:
                variables = {"1": cliente['nombre']}
                enviar_alerta_whatsapp(cliente['telefono'], TPL_FUGA_CORRIENTE, variables)
    except Exception as e:
        print(f"❌ ERROR al consultar fuga de corriente para {cliente['device_id']}: {e}")

def verificar_consumo_nocturno(client_influx, cliente):
    """Revisa si hay consumo elevado sostenido durante la noche."""
    hora_actual = datetime.now().hour
    if not (23 <= hora_actual or hora_actual < 5):
        print("-> No es horario de revisión de consumo nocturno.")
        return
    print("-> Verificando consumo nocturno...")
    device_id_esc = str(cliente['device_id']).replace("'", "''")
    query = f"""SELECT MEAN(corriente_fase) AS promedio_corriente FROM "energia" WHERE "deviceId" = '{device_id_esc}' AND time >= now() - interval '30 minute'"""
    try:
        df = client_influx.query(query=query, language="sql").to_pandas()
        if not df.empty and pd.notna(df["promedio_corriente"].iloc[0]):
            corriente_promedio = float(df["promedio_corriente"].iloc[0])
            if corriente_promedio > UMBRAL_CONSUMO_NOCTURNO:
                variables = {"1": cliente['nombre']}
                enviar_alerta_whatsapp(cliente['telefono'], TPL_CONSUMO_NOCTURNO, variables)
    except Exception as e:
        print(f"❌ ERROR al consultar consumo nocturno para {cliente['device_id']}: {e}")

def verificar_niveles_tarifa(client_influx, cliente):
    """Revisa si se ha cruzado un límite de tarifa o el umbral de DAC."""
    print("-> Verificando niveles de tarifa...")
    if cliente['tipo_tarifa'] in ['PDBT', 'DAC']:
        print(f"   Cliente con tarifa plana ({cliente['tipo_tarifa']}). Se omite verificación.")
        return

    hoy = date.today()
    ultima_fecha_de_corte, _ = calcular_fechas_corte(hoy, cliente['dia_de_corte'], cliente['ciclo_bimestral'])
    if not ultima_fecha_de_corte:
        return

    kwh_acumulados = obtener_consumo_kwh_en_rango(client_influx, cliente['device_id'], ultima_fecha_de_corte, hoy)
    if kwh_acumulados is None:
        return

    # Chequeo de cambio de nivel
    if not cliente['notificacion_nivel_tarifa'] and kwh_acumulados > LIMITE_TARIFA_BASICA:
        precios = {'01': (1.03, 1.30), '01A': (1.03, 1.30)}
        precio_basico, precio_intermedio = precios.get(cliente['tipo_tarifa'], (0, 0))
        variables = {"1": cliente['nombre'], "2": f"{precio_basico:.2f}", "3": f"{precio_intermedio:.2f}"}
        enviar_alerta_whatsapp(cliente['telefono'], TPL_CAMBIO_NIVEL, variables)
        marcar_notificacion_enviada(cliente['device_id'], 'nivel_tarifa')

    # Chequeo de umbral DAC
    if not cliente['notificacion_dac'] and kwh_acumulados > LIMITE_DAC:
        variables = {"1": cliente['nombre'], "2": "6.40"}
        enviar_alerta_whatsapp(cliente['telefono'], TPL_DAC, variables)
        marcar_notificacion_enviada(cliente['device_id'], 'dac')

# --- 5. EJECUCIÓN PRINCIPAL ---
def main():
    print("=============================================")
    print(f"--- Iniciando VIGILANTE de Calidad de Energía ({datetime.now()}) ---")
    
    try:
        client_influx = InfluxDBClient3(host=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG, database=INFLUX_BUCKET)
        print("✅ Conexión con InfluxDB exitosa.")
    except Exception as e:
        print(f"❌ ERROR de conexión con InfluxDB. Abortando. Detalles: {e}")
        return

    clientes = obtener_clientes()
    
    if not clientes:
        print("No hay clientes para procesar. Terminando script.")
        return

    for cliente_data in clientes:
        cliente = {
            "device_id": cliente_data[0], "telefono": cliente_data[1], "nombre": cliente_data[2],
            "dia_de_corte": cliente_data[3], "tipo_tarifa": cliente_data[4], "ciclo_bimestral": cliente_data[5],
            "notificacion_nivel_tarifa": cliente_data[6], "notificacion_dac": cliente_data[7]
        }
        
        print(f"\n--- Verificando alertas para: {cliente['nombre']} ({cliente['device_id']}) ---")
        
        verificar_voltaje(client_influx, cliente)
        verificar_fuga_corriente(client_influx, cliente)
        verificar_consumo_nocturno(client_influx, cliente)
        verificar_niveles_tarifa(client_influx, cliente)

    print("\n--- VIGILANTE de Calidad de Energía completado. ---")
    print("=============================================")

if __name__ == "__main__":
    main()