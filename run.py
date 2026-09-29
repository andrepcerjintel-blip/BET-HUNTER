"""CIBERLAB — TIKTOK BET HUNTER. Uso: python run.py [--port 5000] [--db caminho.db] [--no-browser]"""
import argparse
import os
import sys
import threading
import webbrowser

for _s in (sys.stdout, sys.stderr):      # console do Windows (cp850/cp1252) nunca derruba a aplicação por acentos
    try:
        _s.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=5000)
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--db", default=None, help="arquivo SQLite (padrão: data/bethunter.db)")
ap.add_argument("--no-browser", action="store_true")
a = ap.parse_args()

from bethunter import db, envcheck  # noqa: E402
from bethunter.util import export_dir, load_dotenv, log_dir  # noqa: E402
from bethunter.web import create_app  # noqa: E402

load_dotenv()                            # .env opcional (ANTHROPIC_API_KEY); nunca é gravado no banco
app = create_app(a.db)                   # cria banco/tabelas/configurações padrão se não existirem

st = envcheck.static_status()
print("=" * 64)
print(" CIBERLAB - TIKTOK BET HUNTER")
print(f" Endereco : http://{a.host}:{a.port}")
print(f" Banco    : {db._path()}")
print(f" Logs     : {log_dir()}")
print(f" Exportes : {export_dir()}")
print(f" YT-DLP   : {st['YT-DLP']['estado']} | TESSERACT: {st['TESSERACT']['estado']} | ANTHROPIC: {st['ANTHROPIC']['estado']}")
print(f" VISUAL_ANALYSIS: {st['VISUAL_ANALYSIS']['estado']}  (opcional; a aplicacao funciona sem)")
print(" Use o botao TESTAR AMBIENTE na tela para verificar internet e fontes.")
print("=" * 64)
if not a.no_browser:
    threading.Timer(1.5, lambda: webbrowser.open(f"http://{a.host}:{a.port}")).start()
try:
    app.run(host=a.host, port=a.port, threaded=True)
except OSError as e:
    print(f"\nNao foi possivel abrir a porta {a.port} ({e}). Feche a outra instancia ou use: python run.py --port 5001")
    sys.exit(1)
