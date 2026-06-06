#!/usr/bin/env python3
"""
test_env.py — Verifica que el fichero .env se lee correctamente
Uso: python3 test_env.py
"""

import os
import sys
from pathlib import Path

# ── Cargar .env ───────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
except ImportError:
    print("❌  python-dotenv no instalado. Ejecuta: pip install python-dotenv")
    sys.exit(1)

env_path = Path(__file__).parent / ".env"
print(f"\n📄  Buscando .env en: {env_path}")

if not env_path.exists():
    print("❌  Fichero .env no encontrado.")
    print("    Crea uno copiando .env.example y rellena tus valores.")
    sys.exit(1)

print("✅  Fichero .env encontrado.")
load_dotenv(dotenv_path=env_path, override=True)

# ── Comprobar variables ───────────────────────────────────────────────────────
print("\n── Variables de entorno ─────────────────────────────────────────")

errores = 0

def check(nombre, obligatoria=True):
    global errores
    valor = os.getenv(nombre, "")
    if valor:
        # Mostrar solo los primeros caracteres por seguridad
        preview = valor[:6] + "..." if len(valor) > 6 else valor
        print(f"  ✅  {nombre:<20} = {preview}")
    elif obligatoria:
        print(f"  ❌  {nombre:<20} → NO configurada (obligatoria)")
        errores += 1
    else:
        print(f"  ⚠️   {nombre:<20} → vacía (opcional)")

check("ESIOS_TOKEN")
check("ARISTON_USER")
check("ARISTON_PASS")
check("ARISTON_GATEWAY", obligatoria=False)
check("HORAS_BARATAS",   obligatoria=False)
check("TEMP_OBJETIVO",   obligatoria=False)
check("GEO_ID",          obligatoria=False)

print("─" * 50)

if errores:
    print(f"\n❌  {errores} variable(s) obligatoria(s) sin configurar. Revisa el .env.\n")
    sys.exit(1)
else:
    print("\n✅  Todas las variables obligatorias están configuradas.\n")
