# -----------------------------------------------------
# Script de Alertas Diarias para LETE (Versión Final con Acumulado Corregido)
# -----------------------------------------------------

# --- 1. Importar las librerías necesarias ---
import psycopg2
from influxdb_client_3 import InfluxDBClient3
import requests
import pandas as pd
import certifi
import os
from datetime import date, timedelta
import json

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
CONTENT_SID_NORMAL = "HX5c049622a5b7b8b7f859d388bfd24570"
CONTENT_SID_AVISO = "HX8b9035a380ec5c9b6438760c896df34a"

# --- Lógica de Negocio ---
IVA = 1.16

# --- 4. FUNCIONES ---

def obtener_clientes():
    """Se conecta a la base de datos de clientes y devuelve una lista de todos."""
    try:
        conn = psycopg2.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, dbname=DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT device_id, telefono_whatsapp, kwh_promedio_diario, nombre, dia_de_corte, tipo_tarifa, fecha_inicio_servicio, ciclo_bimestral FROM clientes")
        lista_clientes = cursor.fetchall()
        cursor.close()
        conn.close()
        print(f"✅ Se encontraron {len(lista_clientes)} clientes en la base de datos.")
        return lista_clientes
    except Exception as e:
        print(f"❌ ERROR al conectar con la base de datos de clientes: {e}")
        return []

def obtener_consumo_kwh_en_rango(client_influx, device_id, fecha_inicio, fecha_fin):
    """Obtiene el total de kWh consumidos en un rango de fechas específico."""
    device_id_esc = str(device_id).replace("'", "''")
    fecha_inicio_str = fecha_inicio.strftime('%Y-%m-%d')
    # Sumamos un día a la fecha fin para incluir el rango completo del último día
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

def calcular_costo_estimado(kwh_consumidos, tarifa):
    """Calcula el costo aproximado del recibo de CFE."""
    costo_sin_iva = 0.0
    if tarifa == '01':
        if kwh_consumidos <= 150:
            costo_sin_iva = kwh_consumidos * 1.08
        elif kwh_consumidos <= 280:
            costo_sin_iva = (150 * 1.08) + ((kwh_consumidos - 150) * 1.32)
        else:
            costo_sin_iva = (150 * 1.08) + (130 * 1.32) + ((kwh_consumidos - 280) * 3.85)
    elif tarifa == '01A':
        if kwh_consumidos <= 150:
            costo_sin_iva = kwh_consumidos * 1.08
        elif kwh_consumidos <= 300:
            costo_sin_iva = (150 * 1.08) + ((kwh_consumidos - 150) * 1.32)
        else:
            costo_sin_iva = (150 * 1.08) + (150 * 1.32) + ((kwh_consumidos - 300) * 3.85)
    elif tarifa == 'PDBT':
        costo_sin_iva = kwh_consumidos * 5.60
    return costo_sin_iva * IVA

def actualizar_promedio_cliente(device_id, nuevo_promedio):
    """Actualiza el kwh_promedio_diario para un cliente en Supabase."""
    print(f"ACTUALIZANDO promedio para {device_id} a {nuevo_promedio:.2f} kWh/día...")
    try:
        conn = psycopg2.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, dbname=DB_NAME)
        cursor = conn.cursor()
        sql_update_query = "UPDATE clientes SET kwh_promedio_diario = %s WHERE device_id = %s"
        cursor.execute(sql_update_query, (nuevo_promedio, device_id))
        conn.commit()
        print(f"✅ Promedio para {device_id} actualizado exitosamente.")
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"❌ ERROR al actualizar el promedio para {device_id}: {e}")

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

# --- 5. EJECUCIÓN PRINCIPAL ---
def main():
    print("=============================================")
    print("--- Iniciando Script de Reporte Diario LETE ---")
    
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

    hoy = date.today()
    ayer = hoy - timedelta(days=1)

    for cliente in clientes:
        device_id, telefono, kwh_promedio, nombre, dia_de_corte, tipo_tarifa, fecha_inicio_servicio, ciclo_bimestral = cliente
        print(f"\n--- Procesando cliente: {nombre} ({device_id}) ---")

        ultima_fecha_de_corte, proxima_fecha_de_corte = calcular_fechas_corte(hoy, dia_de_corte, ciclo_bimestral)
        if not ultima_fecha_de_corte:
            print(f"⚠️ No se pudo determinar la fecha de corte para {nombre}. Omitiendo cliente.")
            continue
        
        print(f"Día de corte: {dia_de_corte}, Ciclo: {ciclo_bimestral}. Última fecha de corte calculada: {ultima_fecha_de_corte}")

        if hoy == proxima_fecha_de_corte:
            print(f"¡Fin de periodo detectado para {nombre}! Actualizando promedio...")
            consumo_bimestre_pasado = obtener_consumo_kwh_en_rango(client_influx, device_id, ultima_fecha_de_corte, proxima_fecha_de_corte)
            if consumo_bimestre_pasado is not None and consumo_bimestre_pasado > 0:
                dias_del_bimestre = (proxima_fecha_de_corte - ultima_fecha_de_corte).days
                nuevo_promedio = consumo_bimestre_pasado / dias_del_bimestre if dias_del_bimestre > 0 else 0
                actualizar_promedio_cliente(device_id, nuevo_promedio)
                kwh_promedio = nuevo_promedio

        # --- OBTENER DATOS PARA EL REPORTE ---
        kwh_periodo_actual = obtener_consumo_kwh_en_rango(client_influx, device_id, ultima_fecha_de_corte, hoy)
        
        # ---------------------------------------------------------------------------------
        # --- CORRECCIÓN FINAL: La fecha final para el consumo de AYER debe ser AYER ---
        # ---------------------------------------------------------------------------------
        kwh_ayer = obtener_consumo_kwh_en_rango(client_influx, device_id, ayer, ayer)

        if kwh_periodo_actual is not None and kwh_ayer is not None:
            # --- PREPARACIÓN DEL MENSAJE ---
            dias_transcurridos = (hoy - ultima_fecha_de_corte).days
            if dias_transcurridos <= 0: dias_transcurridos = 1
            proyeccion_kwh = (kwh_periodo_actual / dias_transcurridos) * 60
            costo_estimado = calcular_costo_estimado(proyeccion_kwh, tipo_tarifa)
            
            promedio_float = float(kwh_promedio)
            porcentaje_dif = 0
            if promedio_float > 0:
                porcentaje_dif = ((kwh_ayer - promedio_float) / promedio_float) * 100

            linea_comparativa = ""
            if kwh_ayer > promedio_float:
                linea_comparativa = f"Es un {abs(porcentaje_dif):.0f}% más que tu promedio diario. 📈"
            else:
                linea_comparativa = f"¡Excelente! Ahorraste un {abs(porcentaje_dif):.0f}% respecto a tu promedio diario. 📉"

            # --- LÓGICA PARA ELEGIR LA PLANTILLA CORRECTA ---
            content_sid_a_usar = CONTENT_SID_NORMAL
            if fecha_inicio_servicio and (hoy - fecha_inicio_servicio).days < 60:
                content_sid_a_usar = CONTENT_SID_AVISO
            
            variables_plantilla = {
                "1": nombre,
                "2": f"{kwh_ayer:.2f}",
                "3": linea_comparativa,
                "4": f"{kwh_periodo_actual:.2f}",
                "5": f"{costo_estimado:.2f}"
            }
            
            enviar_alerta_whatsapp(telefono, content_sid_a_usar, variables_plantilla)

    print("\n--- Script de Reporte Diario LETE completado. ---")
    print("=============================================")

if __name__ == "__main__":
    main()