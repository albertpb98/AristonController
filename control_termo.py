#!/usr/bin/env python3
"""
control_termo.py — Lee schedule.json y enciende/apaga el termo según la hora actual
Se ejecuta cada hora via GitHub Actions.
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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

ARISTON_USER    = os.getenv("ARISTON_USER", "")
ARISTON_PASS    = os.getenv("ARISTON_PASS", "")
ARISTON_GATEWAY = os.getenv("ARISTON_GATEWAY", "")
TZ              = ZoneInfo("Europe/Madrid")


def leer_schedule() -> dict:
    path = Path(__file__).parent / "schedule.json"
    if not path.exists():
        print("❌  schedule.json no encontrado. Ejecuta primero update_schedule.py")
        sys.exit(1)
    return json.loads(path.read_text())


async def conectar():
    devs = await ariston.async_discover(ARISTON_USER, ARISTON_PASS)
    if not devs:
        raise Exception("No se encontraron dispositivos.")
    gateway = ARISTON_GATEWAY or devs[0].get("gw", "")
    d = await ariston.async_hello(ARISTON_USER, ARISTON_PASS, gateway, True, "Europe/Madrid")
    await d.async_get_features()
    await d.async_update_state()
    return d


async def main():
    ahora       = datetime.now(TZ)
    hora_actual = ahora.hour
    fecha_hoy   = ahora.date().isoformat()

    print(f"⏰  Ejecutando control_termo — {ahora.strftime('%Y-%m-%d %H:%M')} (hora local)")

    # ── Leer programación ─────────────────────────────────────────────────────
    schedule = leer_schedule()
    fecha_schedule   = schedule.get("date")
    horas_baratas    = schedule.get("horas_baratas", [])
    temp_objetivo    = schedule.get("temp_objetivo", 55.0)

    print(f"📅  Schedule cargado para: {fecha_schedule}")
    print(f"🔥  Horas baratas: {horas_baratas}")

    # Verificar que el schedule es de hoy
    if fecha_schedule != fecha_hoy:
        print(f"⚠️   El schedule es de {fecha_schedule}, hoy es {fecha_hoy}.")
        print("    Puede que update_schedule.py aún no haya corrido hoy.")
        print("    Usando el schedule disponible de todos modos.")

    # ── Decidir encender o apagar ─────────────────────────────────────────────
    encender = hora_actual in horas_baratas
    print(f"🕐  Hora actual: {hora_actual:02d}:00 → {'🔥 ENCENDER' if encender else '💤 APAGAR'}")

    # ── Conectar y actuar ─────────────────────────────────────────────────────
    print("Conectando con Ariston Cloud...", end=" ", flush=True)
    try:
        d = await conectar()
        print("✅")
    except Exception as e:
        print(f"❌  {e}")
        sys.exit(1)

    estado_actual = d.water_heater_power_value
    temp_actual   = d.water_heater_current_temperature
    print(f"Estado actual: {'Encendido' if estado_actual else 'Apagado'} | {temp_actual} °C")

    # Solo actuar si el estado es distinto al deseado
    if encender == estado_actual:
        print(f"✅  El termo ya está {'encendido' if encender else 'apagado'}. Sin cambios.")
        return

    try:
        if encender:
            await d.async_set_power(True)
            await d.async_set_water_heater_temperature(temp_objetivo)
            print(f"✅  Termo ENCENDIDO a {temp_objetivo} °C")
        else:
            await d.async_set_power(False)
            print("✅  Termo APAGADO")
    except Exception as e:
        print(f"❌  Error al controlar el termo: {e}")
        sys.exit(1)

asyncio.run(main())
