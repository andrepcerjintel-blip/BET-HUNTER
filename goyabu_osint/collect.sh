#!/usr/bin/env bash
# Coleta PASSIVA e não destrutiva de recursos públicos de goyabu.io.
# Somente GET/HEAD de recursos entregues ao navegador, DNS público, CT logs e arquivos públicos.
# Nunca sobrescreve evidência: cada execução grava com timestamp UTC.
set -u
cd "$(dirname "$0")"
TS=$(date -u +%Y%m%dT%H%M%SZ)
UA="Mozilla/5.0 (X11; Linux x86_64) OSINT-passive"
BASE=https://goyabu.io
DOMAINS="goyabu.io goyabu.to goyabu.com animeyabu.com"
log(){ echo "[$(date -u +%FT%TZ)] $*" | tee -a reports/commands_$TS.log; }
fetch(){ # $1=url $2=nome-base $3=pasta
  local out="$3/${2}_$TS"
  log "curl -sS -L -D $out.headers -o $out $1"
  curl -sS -m 40 -A "$UA" -D "headers/$(basename "$out").headers" -o "$out" "$1" || log "FALHA $1"
}

# FASE 1 - HTML
for p in "" login perfil contato politica-de-cookie politica-de-privacidade; do
  n=${p:-home}; fetch "$BASE/$p" "$n.html" html
done
# Descobre links de animes/episódios na home (somente links públicos)
grep -Eo 'href="https://goyabu\.io/[^"]+"' html/home.html_$TS 2>/dev/null | cut -d'"' -f2 | sort -u > reports/links_home_$TS.txt
grep -E '/(anime|episodio|assistir)' reports/links_home_$TS.txt | head -12 | while read -r u; do
  fetch "$u" "$(echo "$u" | sed 's#https\?://##; s#[^A-Za-z0-9]#_#g').html" html
done

# URLs absolutos e termos-chave
cat html/*_$TS | grep -Eo "https?://[^\"' <>)]+" | sort -u > reports/absolute_urls_$TS.txt
grep -Eino 'goyabu\.to|goyabu\.com|animeyabu|wp-json|admin-ajax|playersData|blogger|blogspot|googlevideo|batchexecute|firebase|sentry|onesignal|GTM-[A-Z0-9]+|G-[A-Z0-9]{6,}|UA-[0-9-]+|ca-pub-[0-9]+|siteKey|measurementId|projectId|apiKey|stripe|paypal|mercadopago|livepix' html/*_$TS > reports/keyword_hits_$TS.txt
grep -Eoh '/wp-content/(themes|plugins)/[^/"]+' html/*_$TS | sort | uniq -c > reports/theme_plugins_$TS.txt

# FASE 2 - WordPress (somente o que responde publicamente; registra status, sem bypass)
for r in wp-json/ wp-json/wp/v2/types wp-json/wp/v2/posts?per_page=5 wp-json/wp/v2/pages?per_page=5 wp-json/wp/v2/media?per_page=5 wp-json/wp/v2/users; do
  fetch "$BASE/$r" "wp_$(echo "$r" | tr '/?=' '___')" pwa
done

# FASE 3 - PWA
for r in manifest.json manifest.webmanifest service-worker.js sw.js firebase-messaging-sw.js OneSignalSDKWorker.js; do
  fetch "$BASE/$r" "$r" pwa
done

# FASE 4 - JS referenciado
grep -Eoh '<script[^>]+src="[^"]+"' html/*_$TS | grep -Eo 'src="[^"]+"' | cut -d'"' -f2 | sort -u > reports/script_src_$TS.txt
while read -r s; do case "$s" in //*) s="https:$s";; /*) s="$BASE$s";; esac
  fetch "$s" "$(echo "$s" | sed 's#https\?://##; s#[?&=]#_#g; s#/#_#g')" js; done < reports/script_src_$TS.txt
grep -Einr -C3 'goyabu\.to|goyabu\.com|animeyabu|playersData|blogger|blogspot|googlevideo|batchexecute|admin-ajax|wp-json|nonce|firebase|onesignal|sentry|dsn|GTM-|UA-|ca-pub-|client_id|projectId|measurementId|appId|apiKey|siteKey' js/ > reports/js_hits_$TS.txt 2>/dev/null

# FASE 5 - Cloudflare email protection (decodificação local)
python3 - "$TS" <<'PY'
import re,glob,sys
ts=sys.argv[1]
def dec(h):
    k=int(h[:2],16); return ''.join(chr(int(h[i:i+2],16)^k) for i in range(2,len(h),2))
with open(f"reports/cfemail_{ts}.txt","w") as o:
    for f in glob.glob(f"html/*_{ts}"):
        t=open(f,errors="ignore").read()
        for m in re.finditer(r'data-cfemail="([0-9a-f]+)"',t): o.write(f"{f}\t{m.group(1)}\t{dec(m.group(1))}\n")
        for m in re.finditer(r'/cdn-cgi/l/email-protection#([0-9a-f]+)',t): o.write(f"{f}\t{m.group(1)}\t{dec(m.group(1))}\n")
PY

# FASE 6 - playersData (apenas estrutura; sem baixar vídeo)
grep -Eho 'playersData[^;]{0,600}' html/*_$TS > reports/playersData_raw_$TS.txt 2>/dev/null

# FASE 10 - DNS
for d in $DOMAINS; do for t in A AAAA CNAME MX TXT NS SOA CAA; do
  { echo "## $d $t"; dig +noall +answer "$d" "$t"; } >> dns/dns_$TS.txt; done; whois "$d" > "dns/whois_${d}_$TS.txt" 2>&1; done
# CT logs (sem brute force)
for d in $DOMAINS; do fetch "https://crt.sh/?q=%25.$d&output=json" "crt_$d.json" ct; done
# FASE 11 - Histórico
for d in $DOMAINS; do
  fetch "https://web.archive.org/cdx/search/cdx?url=$d/*&output=json&fl=timestamp,original,mimetype,digest&collapse=digest&limit=5000" "cdx_$d.json" historical
  fetch "https://urlscan.io/api/v1/search/?q=domain:$d" "urlscan_$d.json" historical
  fetch "https://otx.alienvault.com/api/v1/indicators/domain/$d/passive_dns" "otx_pdns_$d.json" historical
done

# FASE 12 - Hashes
find html js pwa -type f -newer reports/commands_$TS.log 2>/dev/null | xargs -r sha256sum > hashes/sha256_$TS.txt
for u in /wp-content/uploads/2025/11/Bg-Login-scaled.webp /favicon.ico; do fetch "$BASE$u" "$(basename $u)" hashes; done
fetch https://goyabu.to/wp-content/uploads/2025/11/Bg-Login-scaled.webp "goyabu_to_Bg-Login" hashes
sha256sum hashes/*Bg-Login* hashes/favicon* >> hashes/sha256_$TS.txt 2>/dev/null
log "concluído: $TS"
