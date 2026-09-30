import common
import normalize_tiktok as nt
import resolve_urls
from conftest import make_fetcher


def test_parse_count():
    assert common.parse_count("238.2K") == 238200
    assert common.parse_count("109.8K") == 109800
    assert common.parse_count("16.9K") == 16900
    assert common.parse_count("1,2 mi") == 1200000
    assert common.parse_count("31.480") == 31480
    assert common.parse_count("950") == 950
    assert common.parse_count("-") is None          # nunca assume zero
    assert common.parse_count("") is None and common.parse_count(None) is None


def test_normalize_video_photo_profile():
    for u in ["https://www.tiktok.com/@usuario/video/123456", "https://www.tiktok.com/@usuario/photo/123456?lang=pt",
              "https://www.tiktok.com/@Usuario", "@usuario", "tiktok.com/@usuario/"]:
        n = nt.normalize(u)
        assert n["username"] == "usuario" and n["url_perfil"] == "https://www.tiktok.com/@usuario"
        assert n["url_original"] == u
    assert nt.normalize("https://example.com/x") is None and nt.normalize("# comentário") is None


def test_dedupe_and_merge(root):
    lines = ["https://www.tiktok.com/@aa/video/1", "https://www.tiktok.com/@aa/photo/2", "@bb", "lixo http://x.com", "https://vm.tiktok.com/ZM123/"]
    c, rej, short = nt.load_inputs(lines)
    assert [x["username"] for x in c] == ["aa", "bb"] and c[0]["url_original"].endswith("/video/1")
    assert len(rej) == 1 and len(short) == 1
    nt.merge_candidates(c)
    rows = nt.merge_candidates(nt.load_inputs(["@cc", "@aa"])[0])
    assert [r["username"] for r in rows] == ["aa", "bb", "cc"]


def test_link_helpers():
    assert common.link_kind("https://bit.ly/x") == "encurtador" and common.link_kind("https://linktr.ee/u") == "hub"
    assert common.link_kind("https://t.me/abc") == "telegram" and common.link_kind("https://wa.me/55") == "whatsapp"
    assert common.extract_urls("entra encr.pw/AbC e https://x.com/a?b=1.") == ["https://x.com/a?b=1", "https://encr.pw/AbC"] or \
        set(common.extract_urls("entra encr.pw/AbC e https://x.com/a?b=1.")) == {"https://x.com/a?b=1", "https://encr.pw/AbC"}
    a = common.affiliate_params("https://dominio.com/register?inviter=S0FE98&utm_source=tt&x=1")
    assert {(x["parameter"], x["value"], x["strength"]) for x in a} == {("inviter", "S0FE98", "forte"), ("utm_source", "tt", "fraco")}
    assert common.unwrap_tiktok("https://www.tiktok.com/link/v2?aid=1&target=https%3A%2F%2Fbit.ly%2Fz") == "https://bit.ly/z"
    assert common.registrable("a.b.casa.com.br") == "casa.com.br"


def test_resolve_chain_and_errors():
    f = make_fetcher({"https://encr.pw/x": (301, "https://bit.ly/y", ""), "https://bit.ly/y": (302, "https://di-di.me/register?inviter=S0FE98", ""),
                      "https://di-di.me/register?inviter=S0FE98": (200, None, "<html><title>DIDI cassino</title>bônus slots</html>")})
    r = resolve_urls.resolve("https://encr.pw/x", f)
    assert r["redirect_chain"] == ["https://encr.pw/x", "https://bit.ly/y", "https://di-di.me/register?inviter=S0FE98"]
    assert r["final_domain"] == "di-di.me" and r["error"] is None and "DIDI" in r["final_title"]
    loop = resolve_urls.resolve("https://a.co/1", make_fetcher({"https://a.co/1": (302, "https://b.co/2", ""), "https://b.co/2": (302, "https://a.co/1", "")}))
    assert loop["error"] == "loop de redirecionamento"
    blocked = resolve_urls.resolve("https://a.co/1", make_fetcher({"https://a.co/1": (403, None, "")}))
    assert "403" in blocked["error"] and blocked["final_url"] == "https://a.co/1"
    meta = resolve_urls.resolve("https://m.co/1", make_fetcher({"https://m.co/1": (200, None, '<meta http-equiv="refresh" content="0; url=https://z.co/ok">'), "https://z.co/ok": (200, None, "x")}))
    assert meta["final_url"] == "https://z.co/ok"


def test_expand_hub():
    html = '<a href="https://bit.ly/q">a</a><a href="https://linktr.ee/self">x</a><a href="https://t.me/canal">t</a>'
    links, err = resolve_urls.expand_hub("https://linktr.ee/u", make_fetcher({"https://linktr.ee/u": (200, None, html)}))
    assert err is None and "https://bit.ly/q" in links and "https://t.me/canal" in links and "https://linktr.ee/self" not in links
