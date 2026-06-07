#!/usr/bin/env python3
"""
update_schedule.py — Consulta ESIOS y guarda las horas baratas en schedule.json
Se ejecuta cada día a las 21:00 via GitHub Actions.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

ESIOS_TOKEN   = os.getenv("ESIOS_TOKEN", "")
HORAS_BARATAS = int(os.getenv("HORAS_BARATAS", "4"))
GEO_ID        = os.getenv("GEO_ID", "8741")
TZ            = ZoneInfo("Europe/Madrid")


def obtener_precios_manana() -> dict[int, float]:
    if not ESIOS_TOKEN:
        print("❌  ESIOS_TOKEN no configurado.")
        sys.exit(1)

    manana = datetime.now(TZ).date() + timedelta(days=1)
    inicio = datetime(manana.year, manana.month, manana.day,  0, 0, 0, tzinfo=TZ).isoformat(timespec="seconds")
    fin    = datetime(manana.year, manana.month, manana.day, 23, 0, 0, tzinfo=TZ).isoformat(timespec="seconds")

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

    print(f"Consultando ESIOS para {manana}...")
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()

    precios = {}
    for v in r.json().get("indicator", {}).get("values", []):
        dt_local = datetime.fromisoformat(v["datetime_utc"].replace("Z", "+00:00")).astimezone(TZ)
        precios[dt_local.hour] = v["value"] / 1000.0

    print(f"✅  {len(precios)} horas obtenidas.")
    return precios


def seleccionar_horas_baratas(precios: dict[int, float], n: int) -> list[int]:
    return sorted([h for h, _ in sorted(precios.items(), key=lambda x: x[1])[:n]])


if __name__ == "__main__":
    precios = obtener_precios_manana()
    horas   = seleccionar_horas_baratas(precios, HORAS_BARATAS)
    manana  = (datetime.now(TZ).date() + timedelta(days=1)).isoformat()

    # Guardar en schedule.json
    schedule = {
        "date":           manana,
        "horas_baratas":  horas,
        "temp_objetivo":  float(os.getenv("TEMP_OBJETIVO", "55")),
        "precios":        {str(h): round(v * 100, 4) for h, v in precios.items()},
    }

    output_path = Path(__file__).parent / "schedule.json"
    output_path.write_text(json.dumps(schedule, indent=2, ensure_ascii=False))

    print(f"✅  Horas baratas para {manana}: {horas}")
    print(f"    Guardado en {output_path}")

    # Mostrar tabla resumen
    p_vals  = list(precios.values())
    p_media = sum(p_vals) / len(p_vals)
    print(f"\n  Hora   c€/kWh")
    print("  " + "─" * 25)
    for h in range(24):
        p = precios.get(h)
        if p is None:
            continue
        marca = "🔥" if h in horas else "  "
        print(f"  {h:02d}:00  {p*100:6.2f}  {marca}")
