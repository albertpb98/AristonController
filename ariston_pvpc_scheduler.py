#!/usr/bin/env python3
"""
Ariston Velis Dry — Control directo por precio PVPC (ESIOS / REE)
==================================================================
Cada día a las 21:00 consulta los precios PVPC del día siguiente,
selecciona las horas más baratas y programa encendidos/apagados
directos del termo usando async_set_power.

REQUISITOS
----------
    pip install ariston requests python-dotenv schedule

CONFIGURACIÓN
-------------
Fichero .env en el mismo directorio:

    ESIOS_TOKEN=tu_token_de_esios
    ARISTON_USER=tu_email@ejemplo.com
    ARISTON_PASS=tu_contraseña
    ARISTON_GATEWAY=          # opcional
    HORAS_BARATAS=4
    TEMP_OBJETIVO=55
    GEO_ID=8741               # 8741=Península 8742=Canarias
                              # 8743=Baleares 8744=Ceuta 8745=Melilla

USO
---
    python ariston_pvpc_scheduler.py
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import schedule
import time

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

try:
    import ariston
except ImportError:
    print("❌  Librería 'ariston' no encontrada. Instala con: pip install ariston")
    sys.exit(1)

# ─── Configuración ─────────────────────────────────────────────────────────────

ESIOS_TOKEN     = os.getenv("ESIOS_TOKEN", "")
ARISTON_USER    = os.getenv("ARISTON_USER", "")
ARISTON_PASS    = os.getenv("ARISTON_PASS", "")
ARISTON_GATEWAY = os.getenv("ARISTON_GATEWAY", "")
HORAS_BARATAS   = int(os.getenv("HORAS_BARATAS", "4"))
TEMP_OBJETIVO   = float(os.getenv("TEMP_OBJETIVO", "55"))
GEO_ID          = os.getenv("GEO_ID", "8741")

TZ_LOCAL        = ZoneInfo("Europe/Madrid")
ESIOS_BASE      = "https://api.esios.ree.es"
INDICADOR_PVPC  = 1001

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ariston_pvpc")


# ─── 1. ESIOS: obtener precios PVPC ────────────────────────────────────────────

def _esios_headers() -> dict:
    return {
        "Accept":       "application/json; application/vnd.esios-api-v1+json",
        "Content-Type": "application/json",
        "Host":         "api.esios.ree.es",
        "x-api-key":    ESIOS_TOKEN,
    }


def obtener_precios_manana() -> dict[int, float]:
    """Descarga los 24 precios PVPC del día siguiente desde ESIOS."""
    if not ESIOS_TOKEN:
        log.error("ESIOS_TOKEN no configurado.")
        return {}

    manana = datetime.now(TZ_LOCAL).date() + timedelta(days=1)
    inicio = datetime(manana.year, manana.month, manana.day,  0, 0, 0, tzinfo=TZ_LOCAL).isoformat(timespec="seconds")
    fin    = datetime(manana.year, manana.month, manana.day, 23, 0, 0, tzinfo=TZ_LOCAL).isoformat(timespec="seconds")

    url = (
        f"{ESIOS_BASE}/indicators/{INDICADOR_PVPC}"
        f"?start_date={inicio}&end_date={fin}&time_trunc=hour&geo_ids[]={GEO_ID}"
    )

    log.info(f"Consultando ESIOS para {manana}...")
    try:
        resp = requests.get(url, headers=_esios_headers(), timeout=15)
        resp.raise_for_status()
    except requests.HTTPError:
        log.error(f"Error HTTP {resp.status_code} desde ESIOS.")
        return {}
    except requests.RequestException as e:
        log.error(f"Error de red: {e}")
        return {}

    valores = resp.json().get("indicator", {}).get("values", [])
    if not valores:
        log.warning("ESIOS no devolvió valores. Los precios se publican a partir de las 20:30.")
        return {}

    precios = {}
    for v in valores:
        try:
            dt_utc   = datetime.fromisoformat(v["datetime_utc"].replace("Z", "+00:00"))
            dt_local = dt_utc.astimezone(TZ_LOCAL)
            precios[dt_local.hour] = v["value"] / 1000.0
        except (KeyError, ValueError):
            continue

    log.info(f"Precios obtenidos: {len(precios)} horas")
    return precios


# ─── 2. Selección de horas baratas ─────────────────────────────────────────────

def seleccionar_horas_baratas(precios: dict[int, float], n: int) -> list[int]:
    if not precios:
        fallback = list(range(2, 2 + n))
        log.warning(f"Sin precios. Fallback nocturno: {fallback}")
        return fallback

    seleccion = sorted([h for h, _ in sorted(precios.items(), key=lambda x: x[1])[:n]])
    log.info(f"Horas baratas seleccionadas: {seleccion}")
    log.info("Precios: " + "  ".join(f"{h:02d}h={precios[h]*100:.2f}c€" for h in seleccion))
    return seleccion


def bloques_continuos(horas: list[int]) -> list[tuple[int, int]]:
    """Agrupa horas consecutivas: [1,2,3,7,8] → [(1,4),(7,9)]"""
    if not horas:
        return []
    horas = sorted(set(horas))
    bloques, inicio, anterior = [], horas[0], horas[0]
    for h in horas[1:]:
        if h != anterior + 1:
            bloques.append((inicio, anterior + 1))
            inicio = h
        anterior = h
    bloques.append((inicio, anterior + 1))
    return bloques


def resumen_precios(precios: dict[int, float], horas_baratas: list[int]) -> None:
    if not precios:
        return
    p_vals  = list(precios.values())
    p_media = sum(p_vals) / len(p_vals)

    print("\n  Hora   c€/kWh   Gráfico")
    print("  " + "─" * 45)
    for h in range(24):
        p = precios.get(h)
        if p is None:
            continue
        marca = "🔥" if h in horas_baratas else "  "
        barra = "█" * int(p * 300)
        print(f"  {h:02d}:00  {p*100:6.2f}  {marca} {barra}")
    print()

    p_selec     = [precios[h] for h in horas_baratas if h in precios]
    p_media_sel = sum(p_selec) / len(p_selec) if p_selec else 0
    ahorro      = (p_media - p_media_sel) * 1.5 * len(horas_baratas)
    log.info(f"Ahorro estimado vs. horas caras: ~{ahorro:.3f} €/día")


# ─── 3. Control del termo ──────────────────────────────────────────────────────

async def _conectar_dispositivo():
    """Conecta y devuelve el dispositivo Ariston con estado cargado."""
    dispositivos = await ariston.async_discover(ARISTON_USER, ARISTON_PASS)
    if not dispositivos:
        raise Exception("No se encontraron dispositivos.")

    gateway = ARISTON_GATEWAY or dispositivos[0].get("gw", "")
    d = await ariston.async_hello(ARISTON_USER, ARISTON_PASS, gateway, True, "Europe/Madrid")
    await d.async_get_features()
    await d.async_update_state()
    return d


async def _set_power(encender: bool):
    """Enciende o apaga el termo."""
    try:
        d = await _conectar_dispositivo()
        await d.async_set_power(encender)
        if encender:
            await d.async_set_water_heater_temperature(TEMP_OBJETIVO)
        estado = "🔥 ENCENDIDO" if encender else "💤 APAGADO"
        log.info(f"Termo {estado} correctamente.")
    except Exception as e:
        log.error(f"Error al {'encender' if encender else 'apagar'} el termo: {e}")


def encender_termo():
    asyncio.run(_set_power(True))


def apagar_termo():
    asyncio.run(_set_power(False))


# ─── 4. Programar encendidos/apagados del día siguiente ───────────────────────

def programar_dia_siguiente():
    """
    Consulta precios PVPC, selecciona horas baratas y registra
    en el scheduler los encendidos y apagados para mañana.
    """
    log.info("=" * 55)
    log.info("🔋  Calculando programación para mañana...")

    precios = obtener_precios_manana()
    horas   = seleccionar_horas_baratas(precios, HORAS_BARATAS)
    bloques = bloques_continuos(horas)

    resumen_precios(precios, horas)

    # Cancelar tareas de encendido/apagado anteriores
    schedule.clear("termo")

    manana = (datetime.now(TZ_LOCAL) + timedelta(days=1)).strftime("%Y-%m-%d")

    for inicio, fin in bloques:
        hora_on  = f"{inicio:02d}:00"
        hora_off = f"{fin:02d}:00" if fin < 24 else "23:59"

        schedule.every().day.at(hora_on).do(encender_termo).tag("termo")
        schedule.every().day.at(hora_off).do(apagar_termo).tag("termo")

        log.info(f"  Bloque programado: {hora_on} → {hora_off}")

    log.info(f"✅  {len(bloques)} bloque(s) programado(s) para {manana}")
    log.info("=" * 55)


# ─── 5. Entry point ────────────────────────────────────────────────────────────

def main():
    if not ARISTON_USER or not ARISTON_PASS:
        log.error("Faltan ARISTON_USER / ARISTON_PASS en .env")
        sys.exit(1)
    if not ESIOS_TOKEN:
        log.error("Falta ESIOS_TOKEN en .env")
        sys.exit(1)

    log.info("🚿  Ariston PVPC Scheduler arrancado.")
    log.info(f"   Horas baratas por día : {HORAS_BARATAS}")
    log.info(f"   Temperatura objetivo  : {TEMP_OBJETIVO} °C")
    log.info(f"   Zona geo PVPC         : {GEO_ID}")

    # Programar la consulta diaria de precios a las 21:00
    schedule.every().day.at("21:00").do(programar_dia_siguiente)

    # Si ya son más de las 20:30, ejecutar ahora para tener mañana cubierto
    ahora = datetime.now(TZ_LOCAL)
    if ahora.hour > 20 or (ahora.hour == 20 and ahora.minute >= 30):
        log.info("Son más de las 20:30 — programando mañana ahora.")
        programar_dia_siguiente()
    else:
        log.info(f"Esperando a las 21:00 para consultar precios.")

    log.info("🕐  Scheduler activo. Pulsa Ctrl+C para salir.")

    try:
        while True:
            schedule.run_pending()
            time.sleep(20)
    except KeyboardInterrupt:
        log.info("👋  Scheduler detenido.")


if __name__ == "__main__":
    main()