"""Normaliza URLs do TikTok (/video/, /photo/, perfil, @usuario) para o perfil e remove duplicatas por username."""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

USER_RE = re.compile(r"tiktok\.com/@([A-Za-z0-9._]{2,24})", re.I)
BARE_USER = re.compile(r"@?([A-Za-z0-9._]{2,24})")
SHORT_TT = re.compile(r"(?:vm|vt)\.tiktok\.com/|tiktok\.com/t/", re.I)
FIELDS = ["username", "url_original", "url_perfil", "first_seen"]


def normalize(raw):
    """-> {'username','url_original','url_perfil'} | None. Preserva a URL original."""
    raw = (raw or "").strip()
    if not raw or raw.startswith("#"):
        return None
    m = USER_RE.search(raw)
    if m:
        user = m.group(1)
    elif not re.search(r"[/:]", raw) and BARE_USER.fullmatch(raw):
        user = BARE_USER.fullmatch(raw).group(1)
    else:
        return None
    user = user.rstrip(".").lower()
    if len(user) < 2:
        return None
    return {"username": user, "url_original": raw, "url_perfil": f"https://www.tiktok.com/@{user}"}


def is_short_link(raw):
    return bool(SHORT_TT.search(raw or ""))


def load_inputs(lines):
    """-> (candidatos únicos por username, linhas não reconhecidas, links curtos do TikTok a resolver)."""
    seen, out, rejected, short = set(), [], [], []
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        n = normalize(ln)
        if n:
            if n["username"] not in seen:
                seen.add(n["username"])
                out.append(n)
        elif is_short_link(ln):
            short.append(ln)
        else:
            rejected.append(ln)
    return out, rejected, short


def merge_candidates(new, path=None):
    """Acrescenta a output/candidates.csv sem duplicar e sem perder os já existentes. -> linhas finais."""
    path = path or common.P("output", "candidates.csv")
    rows = common.read_csv(path)
    have = {r["username"] for r in rows}
    for n in new:
        if n["username"] not in have:
            have.add(n["username"])
            rows.append({**n, "first_seen": common.now_iso()})
    common.write_csv(path, rows, FIELDS)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="arquivo com uma URL/@usuario por linha")
    a = ap.parse_args(argv)
    cands, rej, short = load_inputs(Path(a.input).read_text(encoding="utf-8").splitlines())
    rows = merge_candidates(cands)
    print(f"{len(cands)} perfis únicos | {len(rej)} linhas ignoradas | {len(short)} links curtos | candidates.csv={len(rows)}")


if __name__ == "__main__":
    main()
