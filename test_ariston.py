#!/usr/bin/env python3
"""
test_ariston.py — Lee el estado actual del termo Ariston
Uso: python3 test_ariston.py
"""

import asyncio
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

try:
    import ariston
except ImportError:
    print("❌  Librería 'ariston' no instalada. Ejecuta: pip install ariston")
    sys.exit(1)

ARISTON_USER    = os.getenv("ARISTON_USER", "")
ARISTON_PASS    = os.getenv("ARISTON_PASS", "")
ARISTON_GATEWAY = os.getenv("ARISTON_GATEWAY", "")

if not ARISTON_USER or not ARISTON_PASS:
    print("❌  Faltan ARISTON_USER o ARISTON_PASS en el .env")
    sys.exit(1)

async def test():
    try:
        devs = await ariston.async_discover(ARISTON_USER, ARISTON_PASS)
        if not devs:
            print("❌  No se encontraron dispositivos.")
            return

        gateway = ARISTON_GATEWAY or devs[0].get("gw", "")
        d = await ariston.async_hello(ARISTON_USER, ARISTON_PASS, gateway, True, "Europe/Madrid")

        # Cargar estado completo
        if hasattr(d, "async_get_features"):
            await d.async_get_features()
        if hasattr(d, "async_update_state"):
            await d.async_update_state()

    except Exception as e:
        print(f"❌  Error conectando: {e}")
        return

    nombre       = devs[0].get("name", "termo")
    temp_actual  = d.water_heater_current_temperature
    temp_obj     = d.water_heater_target_temperature
    modo         = d.water_heater_current_mode_text
    encendido    = d.water_heater_power_value
    eco          = d.water_heater_eco_value

    print(f"\n  🚿  {nombre}")
    print(f"  🔌  Estado          : {'Encendido' if encendido else 'Apagado'}")
    print(f"  ⚙️   Modo            : {modo}")
    print(f"  🌡️   Temp. actual    : {temp_actual} °C")
    print(f"  🎯  Temp. objetivo  : {temp_obj} °C")
    print(f"  🍃  Eco             : {'Sí' if eco else 'No'}\n")

asyncio.run(test())