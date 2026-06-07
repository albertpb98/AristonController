#!/usr/bin/env python3
"""
test_power.py — Prueba encendido/apagado del termo con precios de HOY
======================================================================
Obtiene los precios PVPC de hoy, selecciona las horas más baratas,
y programa encendidos/apagados reales para comprobar que todo funciona.

Uso: python3 test_power.py
"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

try:
    import ariston
except ImportError:
    print("❌  Librería 'ariston' no instalada.")
    sys.exit(1)

ESIOS_TOKEN  = os.getenv("ESIOS_TOKEN", "")
ARISTON_USER = os.getenv("ARISTON_USER", "")
ARISTON_PASS = os.getenv("ARISTON_PASS", "")
ARISTON_GATEWAY = os.getenv("ARISTON_GATEWAY", "")
HORAS_BARATAS = int(os.getenv("HORAS_BARATAS", "4"))
TEMP_OBJETIVO = float(os.getenv("TEMP_OBJETIVO", "55"))
GEO_ID        = os.getenv("GEO_ID", "8741")
TZ            = ZoneInfo("Europe/Madrid")


# ─── Precios de HOY ────────────────────────────────────────────────────────────

def obtener_precios_hoy() -> dict[int, float]:
    hoy    = datetime.now(TZ).date()
    inicio = datetime(hoy.year, hoy.month, hoy.day,  0, 0, 0, tzinfo=TZ).isoformat(timespec="seconds")
    fin    = datetime(hoy.year, hoy.month, hoy.day, 23, 0, 0, tzinfo=TZ).isoformat(timespec="seconds")

    url = (
        f"https://api.esios.ree.es/indicators/1001"
        f"?start_date={inicio}&end_date={fin}&time_trunc=hour&geo_ids[]={GEO_ID}"
    )
    headers = {
        "Accept":       "application/json; application/vnd.esios-api-v1+json",
        "Content-Type": "application/json",
        "Host":         "api.esios.ree.es",
        "x-api-key":    ESIOS_TOKEN,
    }

    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()

    precios = {}
    for v in r.json().get("indicator", {}).get("values", []):
        dt_local = datetime.fromisoformat(v["datetime_utc"].replace("Z", "+00:00")).astimezone(TZ)
        precios[dt_local.hour] = v["value"] / 1000.0
    return precios


def seleccionar_horas_baratas(precios, n):
    return sorted([h for h, _ in sorted(precios.items(), key=lambda x: x[1])[:n]])


def bloques_continuos(horas):
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


# ─── Control del termo ─────────────────────────────────────────────────────────

async def conectar():
    devs = await ariston.async_discover(ARISTON_USER, ARISTON_PASS)
    if not devs:
        raise Exception("No se encontraron dispositivos.")
    gateway = ARISTON_GATEWAY or devs[0].get("gw", "")
    d = await ariston.async_hello(ARISTON_USER, ARISTON_PASS, gateway, True, "Europe/Madrid")
    await d.async_get_features()
    await d.async_update_state()
    return d


async def test():
    hora_actual = datetime.now(TZ).hour

    # ── Precios ───────────────────────────────────────────────────────────────
    print("\n📊  Obteniendo precios PVPC de hoy...")
    try:
        precios = obtener_precios_hoy()
    except Exception as e:
        print(f"❌  Error ESIOS: {e}")
        sys.exit(1)

    horas   = seleccionar_horas_baratas(precios, HORAS_BARATAS)
    bloques = bloques_continuos(horas)

    print(f"\n  Hora   c€/kWh   Gráfico")
    print("  " + "─" * 45)
    for h in range(24):
        p = precios.get(h)
        if p is None:
            continue
        marca = "🔥" if h in horas else "  "
        ahora = " ← ahora" if h == hora_actual else ""
        print(f"  {h:02d}:00  {p*100:6.2f}  {marca} {'█' * int(p*300)}{ahora}")

    print(f"\n  Horas baratas : {horas}")
    print(f"  Bloques       : {bloques}")

    deberia_estar_encendido = hora_actual in horas
    print(f"\n  Ahora mismo ({hora_actual:02d}:00) el termo debería estar: "
          f"{'🔥 ENCENDIDO' if deberia_estar_encendido else '💤 APAGADO'}")

    # ── Confirmar antes de actuar ─────────────────────────────────────────────
    print("\n" + "─" * 50)
    respuesta = input("  ¿Aplicar este estado al termo ahora? (s/n): ").strip().lower()
    if respuesta != "s":
        print("  Cancelado. No se ha tocado el termo.\n")
        return

    # ── Conectar y actuar ─────────────────────────────────────────────────────
    print("\n  Conectando con Ariston Cloud...", end=" ", flush=True)
    try:
        d = await conectar()
        print("✅")
    except Exception as e:
        print(f"❌  {e}")
        return

    temp_antes = d.water_heater_current_temperature
    modo_antes = d.water_heater_current_mode_text
    print(f"  Estado actual  : {modo_antes} | {temp_antes} °C")

    # Aplicar encendido o apagado según la hora actual
    try:
        if deberia_estar_encendido:
            await d.async_set_power(True)
            await d.async_set_water_heater_temperature(TEMP_OBJETIVO)
            print(f"  ✅  Termo encendido a {TEMP_OBJETIVO} °C")
        else:
            await d.async_set_power(False)
            print("  ✅  Termo apagado")
    except Exception as e:
        print(f"  ❌  Error: {e}")
        return

    # Verificar estado final
    await d.async_update_state()
    temp_despues = d.water_heater_current_temperature
    power_despues = d.water_heater_power_value
    print(f"  Estado final   : {'Encendido' if power_despues else 'Apagado'} | {temp_despues} °C")
    print()

asyncio.run(test())