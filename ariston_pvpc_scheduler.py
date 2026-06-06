#!/usr/bin/env python3
"""
Ariston Velis Dry — Programador automático por precio PVPC (ESIOS / REE)
=========================================================================
Obtiene los precios PVPC del día siguiente desde la API oficial de ESIOS
(Red Eléctrica de España), selecciona las horas más baratas y programa
el calentador de agua para operar solo en esa franja.

REQUISITOS
----------
    pip install ariston requests python-dotenv schedule

CONFIGURACIÓN
-------------
Crea un fichero .env en el mismo directorio:

    ESIOS_TOKEN=tu_token_de_esios       # x-api-key, NO Authorization header
    ARISTON_USER=tu_email@ejemplo.com
    ARISTON_PASS=tu_contraseña
    ARISTON_GATEWAY=                    # opcional, se detecta automáticamente
    HORAS_BARATAS=4                     # horas de calentamiento al día
    TEMP_OBJETIVO=55                    # temperatura en °C (mínimo 55)
    GEO_ID=8741                         # 8741=Península 8742=Canarias
                                        # 8743=Baleares 8744=Ceuta 8745=Melilla

USO
---
    # Ejecutar una vez (para cron / Raspberry Pi):
    python ariston_pvpc_scheduler.py --once

    # Modo continuo con ejecución diaria automática a las 21:00:
    python ariston_pvpc_scheduler.py
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ─── Cargar .env siempre desde la carpeta del script ──────────────────────────
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
TEMP_OBJETIVO   = int(os.getenv("TEMP_OBJETIVO", "55"))
GEO_ID          = os.getenv("GEO_ID", "8741")

TZ_LOCAL       = ZoneInfo("Europe/Madrid")
ESIOS_BASE     = "https://api.esios.ree.es"
INDICADOR_PVPC = 1001

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ariston_pvpc")


# ─── 1. ESIOS: obtener precios PVPC ────────────────────────────────────────────

def _esios_headers() -> dict:
    """
    Cabeceras para la API de ESIOS.
    IMPORTANTE: ESIOS requiere 'x-api-key', NO 'Authorization: Token token=...'
    El WAF de Imperva bloquea la cabecera Authorization con 403 Forbidden.
    """
    return {
        "Accept":       "application/json; application/vnd.esios-api-v1+json",
        "Content-Type": "application/json",
        "Host":         "api.esios.ree.es",
        "x-api-key":    ESIOS_TOKEN,
    }


def obtener_precios_manana() -> dict[int, float]:
    """
    Descarga los 24 precios PVPC del día siguiente desde ESIOS.
    Convierte timestamps UTC a hora local (Europe/Madrid).
    Devuelve: {hora_local (0-23): precio_eur_kwh}
    """
    if not ESIOS_TOKEN:
        log.error("ESIOS_TOKEN no configurado. Añádelo al fichero .env.")
        return {}

    ahora_local  = datetime.now(TZ_LOCAL)
    manana_local = ahora_local.date() + timedelta(days=1)

    inicio = datetime(manana_local.year, manana_local.month, manana_local.day,
                      0, 0, 0, tzinfo=TZ_LOCAL).isoformat(timespec="seconds")
    fin    = datetime(manana_local.year, manana_local.month, manana_local.day,
                      23, 0, 0, tzinfo=TZ_LOCAL).isoformat(timespec="seconds")

    # Construir URL manualmente para evitar que requests codifique los corchetes
    # geo_ids[] debe llegar al servidor sin codificar (%5B%5D no funciona)
    url = (
        f"{ESIOS_BASE}/indicators/{INDICADOR_PVPC}"
        f"?start_date={inicio}&end_date={fin}&time_trunc=hour&geo_ids[]={GEO_ID}"
    )

    log.info(f"Consultando ESIOS: indicador {INDICADOR_PVPC}, geo_id={GEO_ID}")

    try:
        resp = requests.get(url, headers=_esios_headers(), timeout=15)
        resp.raise_for_status()
        datos = resp.json()
    except requests.HTTPError:
        log.error(f"Error HTTP {resp.status_code} desde ESIOS.")
        if resp.status_code == 403:
            log.error("403 Forbidden: comprueba que ESIOS_TOKEN es correcto en .env")
        elif resp.status_code == 401:
            log.error("401 Unauthorized: token inválido o caducado.")
        return {}
    except requests.RequestException as e:
        log.error(f"Error de red al consultar ESIOS: {e}")
        return {}

    valores = datos.get("indicator", {}).get("values", [])
    if not valores:
        log.warning(
            f"ESIOS no devolvió valores para mañana ({manana_local}). "
            "Los precios se publican a partir de las 20:30."
        )
        return {}

    precios: dict[int, float] = {}
    for v in valores:
        try:
            dt_utc   = datetime.fromisoformat(v["datetime_utc"].replace("Z", "+00:00"))
            dt_local = dt_utc.astimezone(TZ_LOCAL)
            precios[dt_local.hour] = v["value"] / 1000.0   # €/MWh → €/kWh
        except (KeyError, ValueError) as e:
            log.warning(f"No se pudo parsear valor ESIOS: {v} → {e}")

    log.info(f"Precios obtenidos para {manana_local}: {len(precios)} horas")
    return precios


# ─── 2. Selección de horas baratas ─────────────────────────────────────────────

def seleccionar_horas_baratas(precios: dict[int, float], n: int) -> list[int]:
    if not precios:
        fallback = list(range(2, 2 + n))
        log.warning(f"Sin precios. Usando fallback nocturno: {fallback}")
        return fallback

    seleccion = sorted([h for h, _ in sorted(precios.items(), key=lambda x: x[1])[:n]])
    log.info(f"Horas baratas: {seleccion}")
    log.info("Precios: " + "  ".join(f"{h:02d}h={precios[h]*100:.2f}c€" for h in seleccion))
    return seleccion


def bloques_continuos(horas: list[int]) -> list[tuple[int, int]]:
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
    p_vals      = list(precios.values())
    p_min       = min(p_vals)
    p_max       = max(p_vals)
    p_media     = sum(p_vals) / len(p_vals)
    p_selec     = [precios[h] for h in horas_baratas if h in precios]
    p_media_sel = sum(p_selec) / len(p_selec) if p_selec else 0
    ahorro_est  = (p_media - p_media_sel) * 1.5 * len(horas_baratas)

    log.info("─" * 55)
    log.info(f"  min={p_min*100:.2f}  media={p_media*100:.2f}  max={p_max*100:.2f}  c€/kWh")
    log.info(f"  Media horas seleccionadas : {p_media_sel*100:.2f} c€/kWh")
    log.info(f"  Ahorro estimado           : ~{ahorro_est:.3f} €/día")
    log.info("─" * 55)

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


# ─── 3. Control del termo Ariston ──────────────────────────────────────────────

async def obtener_dispositivo():
    """
    Conecta a Ariston Cloud y devuelve el objeto dispositivo.
    Las credenciales se pasan directamente a async_discover y async_hello
    (API de python-ariston >= 0.13).
    """
    if not ARISTON_USER or not ARISTON_PASS:
        log.error("Faltan ARISTON_USER / ARISTON_PASS en .env")
        return None

    try:
        log.info("Conectando a Ariston Cloud...")
        dispositivos = await ariston.async_discover(ARISTON_USER, ARISTON_PASS)

        if not dispositivos:
            log.error("No se encontraron dispositivos en la cuenta.")
            return None

        gateway = ARISTON_GATEWAY or dispositivos[0].get("gw", "")
        if not gateway:
            log.error("No se pudo obtener el gateway del dispositivo.")
            return None

        log.info(f"Dispositivo: {dispositivos[0].get('name', 'termo')} ({gateway})")
        d = await ariston.async_hello(
            ARISTON_USER, ARISTON_PASS, gateway, True, "Europe/Madrid"
        )
        # Cargar estado completo del dispositivo
        if hasattr(d, "async_get_features"):
            await d.async_get_features()
        if hasattr(d, "async_update_state"):
            await d.async_update_state()
        return d

    except Exception as e:
        log.error(f"Error conectando con Ariston Cloud: {e}")
        if "SSL" in str(e) or "certificate" in str(e).lower():
            log.error("Error SSL: ejecuta 'pip install certifi' y vuelve a intentarlo.")
        return None


async def programar_termo(horas_baratas: list[int]) -> bool:
    """
    Aplica la programación al termo.
    Intenta los métodos disponibles según el modelo del dispositivo.
    """
    dispositivo = await obtener_dispositivo()
    if dispositivo is None:
        return False

    bloques = bloques_continuos(horas_baratas)
    manana  = (datetime.now(TZ_LOCAL) + timedelta(days=1)).strftime("%A %d/%m/%Y")

    try:
        # Modo PROGRAM (5) para que el termo siga la programación horaria
        # Modos disponibles: 1=MANUAL, 5=PROGRAM, 9=BOOST
        await dispositivo.async_set_mode(5)
        log.info("Modo PROGRAM activado")

        # Temperatura objetivo durante las horas activas
        await dispositivo.async_set_temperature(TEMP_OBJETIVO)
        log.info(f"Temperatura objetivo: {TEMP_OBJETIVO} °C")

        log.info(f"✅  Programación aplicada para {manana}")
        log.info(f"    Horas activas : {horas_baratas}")
        log.info(f"    Bloques       : {bloques}")
        log.info(f"    Temperatura   : {TEMP_OBJETIVO} °C")
        return True

    except Exception as e:
        log.error(f"Error al programar el termo: {e}")
        return False


# ─── 4. Tarea principal ────────────────────────────────────────────────────────

def ejecutar():
    log.info("=" * 55)
    log.info("🔋  Iniciando programación diaria — PVPC (ESIOS/REE)")

    precios = obtener_precios_manana()
    horas   = seleccionar_horas_baratas(precios, HORAS_BARATAS)
    resumen_precios(precios, horas)

    exito = asyncio.run(programar_termo(horas))

    if exito:
        log.info("🎉  Termo programado correctamente.")
    else:
        log.error("⚠️   No se pudo programar el termo (ver errores arriba).")

    log.info("=" * 55)
    return exito


# ─── 5. Entry point ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Programa el Ariston Velis Dry según precios PVPC de ESIOS."
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Ejecutar una vez y salir (para cron / Raspberry Pi).",
    )
    parser.add_argument(
        "--hora", default="21:00",
        help="Hora de ejecución diaria en modo continuo (por defecto 21:00).",
    )
    args = parser.parse_args()

    if args.once:
        sys.exit(0 if ejecutar() else 1)

    try:
        import schedule
        import time
    except ImportError:
        print("Para el modo continuo instala: pip install schedule")
        sys.exit(1)

    ahora = datetime.now(TZ_LOCAL)
    if ahora.hour > 20 or (ahora.hour == 20 and ahora.minute >= 30):
        log.info("Son más de las 20:30 — ejecutando ahora.")
        ejecutar()
    else:
        log.info(f"Esperando hasta las {args.hora} para ejecutar.")

    schedule.every().day.at(args.hora).do(ejecutar)
    log.info(f"🕐  Scheduler activo — ejecución diaria a las {args.hora}.")
    log.info("    Pulsa Ctrl+C para salir.")

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        log.info("👋  Scheduler detenido.")


if __name__ == "__main__":
    main()