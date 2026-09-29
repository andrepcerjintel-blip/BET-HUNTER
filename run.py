"""CIBERLAB — TIKTOK BET HUNTER. Uso: python run.py [--port 5000] [--db caminho.db]"""
import argparse
import os
import webbrowser

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=5000)
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--db", default=None, help="arquivo SQLite (padrão: data/bethunter.db)")
ap.add_argument("--no-browser", action="store_true")
a = ap.parse_args()

from bethunter.web import create_app  # noqa: E402

app = create_app(a.db)
if not a.no_browser:
    try:
        webbrowser.open(f"http://{a.host}:{a.port}")
    except Exception:
        pass
print(f"CIBERLAB — TIKTOK BET HUNTER em http://{a.host}:{a.port}  (banco: {os.environ.get('BETHUNTER_DB', 'data/bethunter.db')})")
app.run(host=a.host, port=a.port, threaded=True)
