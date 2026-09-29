"""Teste operacional curto da descoberta: roda as consultas prioritárias no TikTok Search Local e mede o resultado.
Uso:  python demo/medir_descoberta.py                 (usa a URL de CONFIG; serviço real em http://127.0.0.1:8000)
      python demo/medir_descoberta.py --url http://127.0.0.1:8000 --queries 12
      python demo/medir_descoberta.py --stub          (serviço SIMULADO local: valida o pipeline, NÃO mede o TikTok real)
Usa um banco temporário (não mexe no seu data/bethunter.db)."""
import argparse
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ap = argparse.ArgumentParser()
ap.add_argument("--url", default=None)
ap.add_argument("--queries", type=int, default=12)
ap.add_argument("--stub", action="store_true")
a = ap.parse_args()
os.environ["BETHUNTER_DB"] = os.path.join(tempfile.mkdtemp(), "medida.db")
os.environ["BETHUNTER_LOG_DIR"] = tempfile.mkdtemp()

from bethunter import db, net, pipeline, tiktok_local  # noqa: E402
from bethunter.util import setup_logging  # noqa: E402

setup_logging()      # detalhes técnicos vão para o arquivo de log, não para o console


class Stub(BaseHTTPRequestHandler):
    """Imita o contrato do serviço (page_token, 20/página, fim com page_token null). Dados FICTÍCIOS."""
    def log_message(self, *a): pass
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        self._send(200, {"status": "ok", "device_count": 1, "capacity_remaining_today": 500})
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["content-length"])))
        pg = int((req.get("page_token") or "p0")[1:])
        base = sum(map(ord, req["query"]))
        recs = [{"id": str(70000000000000000 + base * 100 + pg * 20 + i), "description": f"{req['query']} link na bio cadastre-se saque #tigrinho #plat{(base + i) % 9}",
                 "create_time": "2026-09-25T10:00:00+00:00", "author_username": f"demo_user_{(base * 3 + pg * 20 + i) % 400}",
                 "author_id": "1", "region_code": "BR", "view_count": 100, "like_count": 1, "comment_count": 0, "share_count": 0,
                 "hashtags": ["tigrinho", f"plat{(base + i) % 9}"], "source_term": "search:" + req["query"]} for i in range(20)]
        self._send(200, {"count": 20, "next_cursor": 20, "has_more": pg < 2, "page_token": f"p{pg + 1}" if pg < 2 else None, "results": recs})


db.init_db()
net.configure(min_interval=0)
if a.stub:
    srv = HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    a.url = f"http://127.0.0.1:{srv.server_port}"
    print("*** SERVIÇO SIMULADO (dados fictícios): mede o pipeline do RINO, não o TikTok real ***")
with db.connect() as c:      # os providers leem as configurações do banco (temporário)
    s = db.save_settings(c, {"request_delay": 0, **({"tiktok_search_local_url": a.url} if a.url else {})})
st = tiktok_local.check(s)
print(f"TIKTOK SEARCH LOCAL: {st['estado']} — {st['detalhe']}")
if st["estado"] != "OK":
    sys.exit("Serviço indisponível: suba o TikTok Search Local (veja o README) e rode de novo.")
qs = s["priority_queries"][: a.queries]
t0 = time.time()
raw = errs = zero = 0
for q in qs:
    r = pipeline.run_query(q, "tiktok_local", s, hunt="medida")
    raw += r["found"]; errs += 1 if r.get("error") else 0; zero += 1 if (r["found"] == 0 and not r.get("error")) else 0
    print(f"  {q!r:24} -> {r['found']:3} resultados · {r['new']:3} candidatos novos" + (f" · ERRO {r['error'][:70]}" if r.get("error") else ""))
dt = time.time() - t0
with db.connect() as c:
    users = c.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    vids = c.execute("SELECT COUNT(DISTINCT url_video) FROM evidences WHERE kind='video'").fetchone()[0]
    tags = set()
    for (h,) in c.execute("SELECT hashtags FROM evidences"):
        tags.update(json.loads(h or "[]"))
    alta = c.execute("SELECT COUNT(*) FROM candidates WHERE classification LIKE 'ALTA%'").fetchone()[0]
    rev = c.execute("SELECT COUNT(*) FROM candidates WHERE status='REVISAR'").fetchone()[0]
print("\nRESULTADO (", len(qs), "consultas prioritárias )")
print(f"  resultados brutos : {raw}   (antes: 17 em 323 consultas)")
print(f"  usuários únicos   : {users}   (antes: 15)")
print(f"  vídeos únicos     : {vids}")
print(f"  hashtags únicas   : {len(tags)}")
print(f"  alta probabilidade: {alta}   em revisão: {rev}   (antes: 0 / 0)")
print(f"  erros: {errs}   consultas com zero resultados: {zero}   tempo: {dt:.1f}s ({dt / max(len(qs), 1):.2f}s/consulta)")
print(f"  resultados/consulta: {raw / max(len(qs), 1):.1f}   (antes: {17 / 323:.2f})")
