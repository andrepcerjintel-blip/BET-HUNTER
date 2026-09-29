"""SQLite: esquema, conexão e configurações. Uma conexão por operação (thread-safe)."""
import json
import os
import sqlite3
from contextlib import contextmanager

from . import config

DB_PATH = os.environ.get("BETHUNTER_DB", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "bethunter.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  profile_url TEXT, profile_id TEXT, display_name TEXT, bio TEXT, bio_link TEXT,
  status TEXT DEFAULT 'NOVO', status_manual INTEGER DEFAULT 0,
  score INTEGER DEFAULT 0, score_base INTEGER DEFAULT 0, base_raw INTEGER DEFAULT 0,
  classification TEXT DEFAULT 'BAIXA RELEVÂNCIA', content_type TEXT DEFAULT 'INDETERMINADO',
  reasons TEXT DEFAULT '[]', base_reasons TEXT DEFAULT '[]', flags TEXT DEFAULT '{}',
  main_evidence TEXT DEFAULT '', priority INTEGER DEFAULT 4, recurring INTEGER DEFAULT 0,
  platforms TEXT DEFAULT '[]', games TEXT DEFAULT '[]', hashtags TEXT DEFAULT '[]', codes TEXT DEFAULT '[]',
  affiliate_ids TEXT DEFAULT '[]', domains TEXT DEFAULT '[]', mentions TEXT DEFAULT '[]', ev_types TEXT DEFAULT '[]',
  video_url TEXT DEFAULT '', link_original TEXT DEFAULT '', link_final TEXT DEFAULT '', domain_final TEXT DEFAULT '',
  sources TEXT DEFAULT '[]', first_source TEXT DEFAULT '', evidence_count INTEGER DEFAULT 0,
  profile_status TEXT DEFAULT 'NAO_COLETADO',
  analyst_note TEXT DEFAULT '', first_seen TEXT, last_seen TEXT, last_analyzed TEXT,
  dup_hits INTEGER DEFAULT 0, merged_from TEXT DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS ix_cand_status ON candidates(status);
CREATE INDEX IF NOT EXISTS ix_cand_score ON candidates(score);
CREATE INDEX IF NOT EXISTS ix_cand_pid ON candidates(profile_id);
CREATE TABLE IF NOT EXISTS evidences(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,            -- bio | video | snippet | link | manual | relacao (relacao NÃO conta como evidência)
  source TEXT, source_url TEXT, query TEXT,
  url_video TEXT DEFAULT '', caption TEXT DEFAULT '', text TEXT DEFAULT '',
  hashtags TEXT DEFAULT '[]', mentions TEXT DEFAULT '[]', tags TEXT DEFAULT '[]',
  collected_at TEXT, dedupe_key TEXT,
  UNIQUE(candidate_id, dedupe_key)
);
CREATE TABLE IF NOT EXISTS links(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
  origin TEXT DEFAULT 'bio',
  url_original TEXT, chain TEXT DEFAULT '[]', url_final TEXT, domain_final TEXT, base_domain TEXT,
  params TEXT DEFAULT '[]', params_raw TEXT DEFAULT '', page_title TEXT DEFAULT '', page_text TEXT DEFAULT '',
  aggregator TEXT DEFAULT '', error TEXT DEFAULT '', collected_at TEXT,
  UNIQUE(candidate_id, url_original, url_final)
);
CREATE TABLE IF NOT EXISTS discoveries(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
  source TEXT, query TEXT, hunt TEXT, url TEXT, found_at TEXT,
  UNIQUE(candidate_id, source, query)
);
CREATE TABLE IF NOT EXISTS cluster_members(
  candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
  tipo TEXT NOT NULL, valor TEXT NOT NULL, extra TEXT DEFAULT '',
  PRIMARY KEY(candidate_id, tipo, valor)
);
CREATE INDEX IF NOT EXISTS ix_cm ON cluster_members(tipo, valor);
CREATE TABLE IF NOT EXISTS hunts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT, kind TEXT DEFAULT 'queries', queries TEXT DEFAULT '[]', sources TEXT DEFAULT '[]',
  enabled INTEGER DEFAULT 1, position INTEGER DEFAULT 0, last_run TEXT
);
CREATE TABLE IF NOT EXISTS search_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, hunt TEXT, query TEXT, source TEXT,
  found INTEGER DEFAULT 0, new INTEGER DEFAULT 0, dups INTEGER DEFAULT 0, errors INTEGER DEFAULT 0,
  error_msg TEXT DEFAULT '', duration_ms INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS visuals(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
  url_video TEXT, thumb_url TEXT, engine TEXT, signals TEXT DEFAULT '{}', text TEXT DEFAULT '', collected_at TEXT,
  UNIQUE(candidate_id, url_video)
);
"""

MIGRATIONS = [("candidates", "visual_analysis", "TEXT DEFAULT 'não disponível'")]


def _path():
    return os.environ.get("BETHUNTER_DB", DB_PATH)


@contextmanager
def connect(path=None):
    path = path or _path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path=None):
    with connect(path) as c:
        c.executescript(SCHEMA)
        for tbl, col, ddl in MIGRATIONS:
            if col not in [r[1] for r in c.execute(f"PRAGMA table_info({tbl})")]:
                c.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {ddl}")
        if not c.execute("SELECT 1 FROM settings WHERE key='migrated_v2'").fetchone():
            # regra antiga (auto-descartar) -> nova: BAIXA RELEVÂNCIA sem decisão do analista
            c.execute("UPDATE candidates SET status='BAIXA RELEVÂNCIA' WHERE status='DESCARTADO' AND status_manual=0")
            c.execute("INSERT INTO settings(key,value) VALUES('migrated_v2','1')")
        if not c.execute("SELECT 1 FROM hunts WHERE kind='matrix'").fetchone() and c.execute("SELECT 1 FROM hunts LIMIT 1").fetchone():
            c.execute("INSERT INTO hunts(name,kind,queries,sources,enabled,position) VALUES(?,?,?,?,1,99)",
                      (config.DEFAULT_HUNTS[-1][0], "matrix", "[]", json.dumps(config.DEFAULT_HUNT_SOURCES)))
        if not c.execute("SELECT 1 FROM hunts LIMIT 1").fetchone():
            for i, (name, kind, queries) in enumerate(config.DEFAULT_HUNTS):
                c.execute("INSERT INTO hunts(name,kind,queries,sources,enabled,position) VALUES(?,?,?,?,1,?)",
                          (name, kind, json.dumps(queries, ensure_ascii=False),
                           json.dumps(config.DEFAULT_HUNT_SOURCES), i))


def get_settings(conn):
    s = config.default_settings()
    row = conn.execute("SELECT value FROM settings WHERE key='user'").fetchone()
    if row:
        try:
            config.deep_merge(s, json.loads(row["value"]))
        except ValueError:
            pass
    return s


def save_settings(conn, patch):
    """Mescla `patch` nas configurações do usuário (só guarda o que difere do padrão não é necessário)."""
    row = conn.execute("SELECT value FROM settings WHERE key='user'").fetchone()
    cur = json.loads(row["value"]) if row else {}
    config.deep_merge(cur, patch)
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('user',?)", (json.dumps(cur, ensure_ascii=False),))
    return get_settings(conn)


def jl(v, default=None):
    """json.loads tolerante."""
    if v is None or v == "":
        return default if default is not None else []
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return default if default is not None else []


def jd(v):
    return json.dumps(v, ensure_ascii=False)
