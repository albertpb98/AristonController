#!/usr/bin/env python3
"""
test_esios.py — Verifica la conexión con ESIOS y muestra los precios PVPC de hoy
Uso: python3 test_esios.py
"""

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

ESIOS_TOKEN = os.getenv("ESIOS_TOKEN", "")
GEO_ID      = os.getenv("GEO_ID", "8741")
TZ          = ZoneInfo("Europe/Madrid")

if not ESIOS_TOKEN:
    print("❌  ESIOS_TOKEN no configurado. Añádelo al fichero .env")
    sys.exit(1)

hoy    = datetime.now(TZ).date()
inicio = datetime(hoy.year, hoy.month, hoy.day,  0, 0, 0, tzinfo=TZ).isoformat(timespec="seconds")
fin    = datetime(hoy.year, hoy.month, hoy.day, 23, 0, 0, tzinfo=TZ).isoformat(timespec="seconds")

url = (
    f"https://api.esios.ree.es/indicators/1001"
    f"?start_date={inicio}&end_date={fin}&time_trunc=hour&geo_ids[]={GEO_ID}"
)

headers = {
    "Accept":        "application/json; application/vnd.esios-api-v2+json",
    "Content-Type":  "application/json",
    "Host":          "api.esios.ree.es",
    "x-api-key":     ESIOS_TOKEN,
}

print(f"\n── Test ESIOS — PVPC hoy ({hoy})  geo_id={GEO_ID} ──────────────────")
print(f"  Token   : {ESIOS_TOKEN[:6]}...")
print(f"  URL     : {url}\n")

try:
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
except requests.HTTPError:
    print(f"❌  Error HTTP {r.status_code}: {r.text[:300]}")
    if r.status_code == 403:
        print("\n  El token existe pero ESIOS devuelve 403 Forbidden.")
        print("  Posibles causas:")
        print("  · El token aún no está activado por REE (puede tardar 1-2 días)")
        print("  · El token tiene comillas extra en el .env → debe ser: ESIOS_TOKEN=abc123")
        print("  · La IP desde la que llamas está bloqueada")
    elif r.status_code == 401:
        print("\n  Token inválido. Revisa que lo has copiado correctamente.")
    sys.exit(1)
except requests.RequestException as e:
    print(f"❌  Error de red: {e}")
    sys.exit(1)

print("✅  Conexión con ESIOS correcta\n")

valores = r.json().get("indicator", {}).get("values", [])
if not valores:
    print("⚠️  Sin datos para hoy. Prueba mañana o revisa el geo_id.")
    sys.exit(0)

precios = {}
for v in valores:
    dt_utc   = datetime.fromisoformat(v["datetime_utc"].replace("Z", "+00:00"))
    dt_local = dt_utc.astimezone(TZ)
    precios[dt_local.hour] = v["value"] / 1000.0

p_vals       = list(precios.values())
p_min        = min(p_vals)
p_max        = max(p_vals)
p_media      = sum(p_vals) / len(p_vals)
hora_actual  = datetime.now(TZ).hour

print(f"  {'Hora':<6} {'c€/kWh':>7}   Gráfico")
print("  " + "─" * 52)
for h in sorted(precios):
    p      = precios[h]
    barra  = "█" * int(p * 250)
    ahora  = " ← ahora" if h == hora_actual else ""
    barata = " 🟢" if p <= p_media else ""
    print(f"  {h:02d}:00  {p*100:6.2f}   {barra:<28}{ahora}{barata}")

print("  " + "─" * 52)
print(f"  Mínimo : {p_min*100:.2f} c€/kWh  (hora {min(precios, key=precios.get):02d}:00)")
print(f"  Máximo : {p_max*100:.2f} c€/kWh  (hora {max(precios, key=precios.get):02d}:00)")
print(f"  Media  : {p_media*100:.2f} c€/kWh")
print(f"\n  🟢 = por debajo de la media  |  {len(valores)} horas recibidas\n")