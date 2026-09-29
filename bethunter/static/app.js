/* CIBERLAB — TIKTOK BET HUNTER · interface (JS puro, sem dependências) */
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const jq = s => esc(JSON.stringify(String(s ?? '')));  // literal JS seguro dentro de atributo HTML
const safeUrl = u => /^https?:\/\//i.test(u || '') ? u : '';
const NI = 'NÃO IDENTIFICADO';
const STATUSES = ['NOVO','REVISAR','CONFIRMADO','DESCARTADO','DUPLICADO','PERFIL INDISPONÍVEL','CONTEÚDO REMOVIDO','JÁ ENCAMINHADO'];
const SOURCES = [['ddg','DuckDuckGo'],['bing','Bing'],['tiktok','TikTok Search'],['tiktok_tag','TikTok Hashtag']];
const S = {view:'results', filters:{}, page:1, per:50, sort:'priority', items:[], total:0, sel:new Set(), cur:-1, facets:{}, settings:null};

async function api(path, opt = {}) {
  const o = {method: opt.method || 'GET', headers: {}};
  if (opt.json !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(opt.json); o.method = opt.method || 'POST'; }
  if (opt.form) { o.body = opt.form; o.method = 'POST'; }
  const r = await fetch(path, o);
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) { const e = new Error((data && data.error) || r.statusText); e.status = r.status; e.data = data; throw e; }
  return data;
}
function toast(msg, ms = 3500) {
  const d = document.createElement('div'); d.textContent = msg; $('#toast').appendChild(d); setTimeout(() => d.remove(), ms);
}
async function copyText(t) {
  try { await navigator.clipboard.writeText(t); } catch (e) {
    const ta = document.createElement('textarea'); ta.value = t; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
  }
}
function openUrl(u) { u = safeUrl(u); if (u) window.open(u, '_blank', 'noopener'); else toast('URL não disponível'); }
function modal(html, cls = '') {
  const bg = document.createElement('div'); bg.className = 'modalbg';
  bg.innerHTML = `<div class="modal ${cls}"><button class="x sm" data-close>✕</button>${html}</div>`;
  bg.addEventListener('mousedown', e => { if (e.target === bg) bg.remove(); });
  bg.querySelector('[data-close]').onclick = () => bg.remove();
  $('#modalRoot').appendChild(bg); return bg;
}
const closeModals = () => $('#modalRoot').innerHTML = '';
const fmtDate = s => s ? s.replace('T', ' ') : NI;
const clip = (s, n) => { s = s || ''; return s.length > n ? s.slice(0, n - 1) + '…' : s; };
const scoreCls = it => it.classification && it.classification.startsWith('ALTA') ? 's-alta' : it.classification === 'REVISAR' ? 's-rev' : 's-low';
const lines = t => (t || '').split('\n').map(x => x.trim()).filter(Boolean);

/* ------------------------------------------------------------ painel */
async function loadStats() {
  const s = await api('/api/stats');
  const c = (n, l, cls = '') => `<div class="card ${cls}"><div class="n">${n}</div><div class="l">${l}</div></div>`;
  $('#panel').innerHTML = c(s.total, 'Total coletado') + c(s.alta, 'Alta probabilidade', 'alta') + c(s.revisar, 'Revisar', 'rev') +
    c(s.descartados, 'Descartados') + c(s.confirmados, 'Confirmados', 'conf') + c(s.duplicados, 'Duplicados evitados') +
    c(s.dominios, 'Domínios identificados') + c(s.clusters, 'Clusters identificados') +
    `<div class="card" style="grid-column:span 2"><div class="l">Meta ${s.mission.meta} · confirmados ${s.mission.confirmados}</div><div class="bar" style="margin-top:8px"><i style="width:${s.mission.progresso * 100}%"></i></div><div class="l" style="margin-top:4px">restam ${s.mission.restantes} · em revisão ${s.mission.em_revisao}</div></div>`;
  $('#pendCount').textContent = s.pendentes || '';
  return s;
}

/* ------------------------------------------------------------ navegação */
document.querySelectorAll('#tabs a').forEach(a => a.onclick = () => setView(a.dataset.v));
function setView(v) {
  S.view = v; S.page = 1; S.sel.clear();
  document.querySelectorAll('#tabs a').forEach(a => a.classList.toggle('on', a.dataset.v === v));
  const fn = {results: renderResults, pending: renderResults, clusters: renderClusters, hunts: renderHunts, mission: renderMission,
    metrics: renderMetrics, log: renderLog, config: renderConfig, export: renderExport}[v];
  fn(); loadStats();
}

/* ------------------------------------------------------------ resultados */
function qs(extra = {}) {
  const f = {...S.filters, ...extra};
  return Object.entries(f).filter(([, v]) => v !== '' && v != null && v !== false).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
}
async function loadFacets() { try { S.facets = await api('/api/facets'); } catch (e) {} }
function filterBar() {
  const f = S.filters, opt = (arr, cur, ph) => `<option value="">${ph}</option>` + arr.map(x => `<option ${x === cur ? 'selected' : ''}>${esc(x)}</option>`).join('');
  return `<div class="filters" id="fbar">
   <input class="grow" data-f="q" placeholder="buscar username, bio, evidência, observação…" value="${esc(f.q || '')}">
   <input data-f="min_score" type="number" min="0" max="100" placeholder="score mín." value="${esc(f.min_score || '')}" style="width:85px">
   <select data-f="status">${opt(STATUSES, f.status, 'status: todos')}</select>
   <select data-f="platform">${opt(S.facets.platforms || [], f.platform, 'plataforma')}</select>
   <select data-f="domain">${opt(S.facets.domains || [], f.domain, 'domínio')}</select>
   <select data-f="game">${opt(S.facets.games || [], f.game, 'jogo')}</select>
   <select data-f="hashtag">${opt(S.facets.hashtags || [], f.hashtag, 'hashtag')}</select>
   <select data-f="ev_type">${opt(S.facets.ev_types || [], f.ev_type, 'tipo de evidência')}</select>
   <select data-f="source">${opt(S.facets.sources || [], f.source, 'fonte')}</select>
   <input data-f="cluster" placeholder="cluster (tipo:valor)" value="${esc(f.cluster || '')}" style="width:150px">
   <input data-f="date_from" type="date" title="coletado desde" value="${esc(f.date_from || '')}">
   <input data-f="date_to" type="date" title="coletado até" value="${esc(f.date_to || '')}">
   <label class="i"><input data-f="with_link" type="checkbox" ${f.with_link ? 'checked' : ''}>com link externo</label>
   <label class="i"><input data-f="with_aff" type="checkbox" ${f.with_aff ? 'checked' : ''}>com Affiliate ID/código</label>
   <select id="sortSel"><option value="priority" ${S.sort === 'priority' ? 'selected' : ''}>MAIS IMPORTANTES PRIMEIRO</option>
     <option value="score" ${S.sort === 'score' ? 'selected' : ''}>maior score</option><option value="recent" ${S.sort === 'recent' ? 'selected' : ''}>mais recentes</option>
     <option value="username" ${S.sort === 'username' ? 'selected' : ''}>username A-Z</option></select>
   <button class="sm" id="fclear">limpar filtros</button></div>`;
}
let _deb;
function bindFilters() {
  document.querySelectorAll('#fbar [data-f]').forEach(el => {
    el.oninput = el.onchange = () => {
      const k = el.dataset.f; S.filters[k] = el.type === 'checkbox' ? (el.checked ? '1' : '') : el.value; S.page = 1;
      clearTimeout(_deb); _deb = setTimeout(loadResults, el.tagName === 'INPUT' && el.type !== 'checkbox' ? 350 : 0);
    };
  });
  $('#sortSel').onchange = e => { S.sort = e.target.value; loadResults(); };
  $('#fclear').onclick = () => { S.filters = {}; renderResults(); };
}
async function renderResults() {
  await loadFacets();
  const pend = S.view === 'pending';
  $('#view').innerHTML = (pend ? `<div class="box warn">Itens <b>sem evidência verificável</b> (ex.: só username/URL importados, perfil não coletado). Ficam separados e <b>não podem ser confirmados</b> até haver evidência. Use <i>INVESTIGAR</i>, <i>+ EVIDÊNCIA MANUAL</i> ou abra o perfil e registre o que viu.</div>` : '') +
    filterBar() + `<div class="filters"><span id="selInfo" class="mut"></span>
    <button class="sm ok" onclick="bulk('CONFIRMADO')">confirmar selecionados</button><button class="sm bad" onclick="bulk('DESCARTADO')">descartar selecionados</button>
    <button class="sm" onclick="bulk('REVISAR')">→ revisar</button>
    <button class="sm" onclick="discardBelow()">descartar filtrados abaixo de score…</button>
    <span class="mut">atalhos: <kbd>j</kbd>/<kbd>k</kbd> navegar · <kbd>c</kbd> confirmar · <kbd>d</kbd> descartar · <kbd>e</kbd> evidências · <kbd>o</kbd> perfil · <kbd>v</kbd> vídeo · <kbd>x</kbd> marcar</span></div>
    <div class="tblwrap"><table><thead><tr><th><input type="checkbox" id="selAll"></th><th>Score</th><th>Status</th><th>Username</th><th>Perfil</th><th>Tipo</th><th>Principal evidência</th><th>Plataforma</th><th>Domínio</th><th>Código / Affiliate ID</th><th>Vídeo</th><th>Link externo</th><th>Fonte</th><th>Ações</th></tr></thead><tbody id="tb"></tbody></table></div>
    <div class="pager" id="pager"></div>`;
  bindFilters(); $('#selAll').onchange = e => { S.items.forEach(i => e.target.checked ? S.sel.add(i.id) : S.sel.delete(i.id)); drawRows(); };
  loadResults();
}
async function loadResults() {
  const f = {...S.filters, view: S.view === 'pending' ? 'pending' : 'results'};
  const q = Object.entries(f).filter(([, v]) => v).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
  const r = await api(`/api/candidates?${q}&page=${S.page}&per_page=${S.per}&sort=${S.sort}`);
  S.items = r.items; S.total = r.total; S.cur = r.items.length ? 0 : -1; drawRows();
}
function drawRows() {
  const tb = $('#tb'); if (!tb) return;
  tb.innerHTML = S.items.map((i, idx) => {
    const cls = (S.sel.has(i.id) ? 'sel ' : '') + (i.status === 'CONFIRMADO' ? 'confirmed ' : '') + (i.status === 'DESCARTADO' ? 'discarded ' : '');
    const codes = [...(i.affiliate_ids || []), ...(i.codes || []).map(c => 'código ' + c)];
    return `<tr class="${cls}" data-id="${i.id}" data-idx="${idx}" style="${idx === S.cur ? 'outline:1px solid var(--acc)' : ''}">
     <td><input type="checkbox" data-sel="${i.id}" ${S.sel.has(i.id) ? 'checked' : ''}></td>
     <td><span class="score ${scoreCls(i)}" title="${esc(i.classification)}">${i.score}</span>${i.recurring ? '<br><span class="tag pp">PADRÃO RECORRENTE</span>' : ''}</td>
     <td><select data-st="${i.id}">${STATUSES.map(s => `<option ${s === i.status ? 'selected' : ''}>${s}</option>`).join('')}</select></td>
     <td><b>@${esc(i.username)}</b>${i.pendente ? '<br><span class="tag warn">PENDENTE DE VALIDAÇÃO</span>' : ''}</td>
     <td class="clip" title="${esc(i.display_name)}">${esc(i.display_name || NI)}</td>
     <td><span class="tag">${esc(i.content_type)}</span></td>
     <td class="ev" title="${esc(i.main_evidence)}">${esc(clip(i.main_evidence || NI, 150))}</td>
     <td>${(i.platforms || []).map(p => `<span class="tag">${esc(p)}</span>`).join('') || `<span class="mut">${NI}</span>`}</td>
     <td class="clip" title="${esc(i.domain_final)}">${esc(i.domain_final || NI)}</td>
     <td>${codes.map(c => `<span class="tag">${esc(c)}</span>`).join('') || `<span class="mut">${NI}</span>`}</td>
     <td>${i.video_url ? `<a href="${esc(safeUrl(i.video_url))}" target="_blank" rel="noopener">vídeo</a>` : `<span class="mut">${NI}</span>`}</td>
     <td class="clip" title="${esc(i.link_final)}">${i.link_final ? `<a href="${esc(safeUrl(i.link_final))}" target="_blank" rel="noopener noreferrer">${esc(clip(i.link_final, 34))}</a>` : `<span class="mut">${NI}</span>`}</td>
     <td>${(i.sources || []).map(s => `<span class="tag">${esc(s)}</span>`).join('')}</td>
     <td><div class="rowbtn">
       <button class="sm" onclick="openUrl(${jq(i.profile_url)})">ABRIR PERFIL</button>
       <button class="sm" ${i.video_url ? '' : 'disabled'} onclick="openUrl(${jq(i.video_url)})">ABRIR VÍDEO</button>
       <button class="sm" ${i.link_final ? '' : 'disabled'} onclick="openUrl(${jq(i.link_final)})">ABRIR LINK</button>
       <button class="sm" onclick="showDetail(${i.id})">VER EVIDÊNCIAS</button>
       <button class="sm ok" onclick="setSt(${i.id},'CONFIRMADO')">CONFIRMAR</button>
       <button class="sm bad" onclick="setSt(${i.id},'DESCARTADO')">DESCARTAR</button>
       <button class="sm" onclick="expandId(${i.id})">EXPANDIR</button></div></td></tr>`;
  }).join('') || `<tr><td colspan="14" class="mut" style="padding:24px">Nenhum resultado. Use NOVA BUSCA, INVESTIGAR PERFIL ou IMPORTAR LISTA.</td></tr>`;
  tb.querySelectorAll('[data-sel]').forEach(c => c.onchange = () => { c.checked ? S.sel.add(+c.dataset.sel) : S.sel.delete(+c.dataset.sel); drawRows(); });
  tb.querySelectorAll('[data-st]').forEach(s => s.onchange = () => setSt(+s.dataset.st, s.value));
  $('#selInfo').textContent = `${S.sel.size} selecionados · ${S.total} resultados`;
  const pages = Math.max(1, Math.ceil(S.total / S.per));
  $('#pager').innerHTML = `<button class="sm" ${S.page <= 1 ? 'disabled' : ''} onclick="S.page--;loadResults()">←</button> página ${S.page}/${pages}
    <button class="sm" ${S.page >= pages ? 'disabled' : ''} onclick="S.page++;loadResults()">→</button>`;
}
async function setSt(id, st, note) {
  try { await api(`/api/candidates/${id}/status`, {json: {status: st, note}}); loadResults(); loadStats(); }
  catch (e) { toast('⚠ ' + e.message, 6000); loadResults(); }
}
async function bulk(st) {
  if (!S.sel.size) return toast('Nada selecionado');
  const r = await api('/api/bulk/status', {json: {ids: [...S.sel], status: st}});
  toast(`${r.alterados} alterados` + (r.ignorados_sem_evidencia ? ` · ${r.ignorados_sem_evidencia} ignorados (sem evidência)` : ''));
  S.sel.clear(); loadResults(); loadStats();
}
async function discardBelow() {
  const v = prompt('Descartar TODOS os resultados atualmente filtrados com score abaixo de: (não apaga; apenas muda status para DESCARTADO)', '45');
  if (v === null) return;
  const f = {...S.filters, view: S.view === 'pending' ? 'pending' : 'results', status: 'NOVO|REVISAR'};
  const all = await api(`/api/candidates?${Object.entries(f).filter(([, x]) => x).map(([k, x]) => `${k}=${encodeURIComponent(x)}`).join('&')}&per_page=5000`);
  const ids = all.items.filter(i => i.score < +v).map(i => i.id);
  if (!ids.length) return toast('Nenhum item abaixo desse score');
  if (!confirm(`Descartar ${ids.length} itens?`)) return;
  await api('/api/bulk/status', {json: {ids, status: 'DESCARTADO'}}); loadResults(); loadStats();
}
document.addEventListener('keydown', e => {
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName) || $('#modalRoot').children.length || $('.review-ov')) return;
  if (!['results', 'pending'].includes(S.view) || !S.items.length) return;
  const it = S.items[S.cur]; const k = e.key.toLowerCase();
  if (k === 'j' || k === 'k') { S.cur = Math.max(0, Math.min(S.items.length - 1, S.cur + (k === 'j' ? 1 : -1))); drawRows(); document.querySelector(`tr[data-idx="${S.cur}"]`)?.scrollIntoView({block: 'nearest'}); }
  else if (!it) return;
  else if (k === 'c') setSt(it.id, 'CONFIRMADO'); else if (k === 'd') setSt(it.id, 'DESCARTADO');
  else if (k === 'e') showDetail(it.id); else if (k === 'o') openUrl(it.profile_url); else if (k === 'v') openUrl(it.video_url);
  else if (k === 'x') { S.sel.has(it.id) ? S.sel.delete(it.id) : S.sel.add(it.id); drawRows(); }
});

/* ------------------------------------------------------------ detalhe / evidências */
function reasonsHtml(d) {
  const tot = d.reasons.reduce((a, r) => a + r.pts, 0);
  return `<div class="box"><b>SCORE: ${d.score}</b> <span class="mut">(${esc(d.classification)}${d.score === 100 && tot > 100 ? ` · soma bruta ${tot}, limitado a 100` : ''})</span>
   <div class="mut" style="margin:4px 0">MOTIVOS:</div><ul class="reasons" style="margin:0;padding-left:18px">${d.reasons.map(r =>
    `<li><span class="${r.pts > 0 ? 'pos' : 'neg'}">${r.pts > 0 ? '+' : ''}${r.pts}</span> ${esc(r.label)}${r.detail ? ` <span class="mut">— ${esc(r.detail)}</span>` : ''}</li>`).join('') || '<li class="mut">nenhum sinal encontrado</li>'}</ul>
   <div style="margin-top:4px"><b>TOTAL: ${d.score}</b></div>
   <div class="mut" style="margin-top:4px">O score é apoio à triagem; não conclui ilicitude. A confirmação final é humana.</div></div>`;
}
function linksHtml(d) {
  if (!d.links.length) return `<div class="mut">Nenhum link externo registrado (${NI}).</div>`;
  return d.links.map(l => `<div class="box"><div class="chain">
    <div><span class="mut">URL_ORIGINAL</span> ${esc(l.url_original)}</div>
    ${l.chain.map(c => `<div><span class="mut">URL_INTERMEDIARIA</span> ${esc(c)}</div>`).join('')}
    <div><span class="mut">URL_FINAL</span> ${esc(l.url_final || NI)}</div>
    <div><span class="mut">DOMINIO_FINAL</span> <b>${esc(l.domain_final || NI)}</b> ${l.aggregator ? `<span class="tag">via agregador ${esc(l.aggregator)}</span>` : ''}</div>
    <div><span class="mut">PARAMETROS_URL</span> ${l.params.length ? l.params.map(p => `<span class="tag ${['affiliate','referral'].includes(p.type) ? 'pp' : ''}">${esc(p.param)}=${esc(p.value)} · ${esc(p.type)} · ${esc(p.dominio)}</span>`).join('') : NI}</div>
    ${l.page_title ? `<div><span class="mut">PÁGINA</span> ${esc(l.page_title)}</div>` : ''}${l.error ? `<div class="neg">⚠ ${esc(l.error)}</div>` : ''}</div>
    <div style="margin-top:4px"><button class="sm" onclick="openUrl(${jq(l.url_final)})">ABRIR LINK</button>
    ${l.domain_final ? `<button class="sm" onclick="searchInd('domínio',${jq(l.domain_final)})">BUSCAR ESTE DOMÍNIO</button>` : ''}</div></div>`).join('');
}
async function showDetail(id, host) {
  const d = await api(`/api/candidates/${id}`);
  const html = `<h2>@${esc(d.username)} <span class="mut">${esc(d.display_name || '')}</span> ${d.pendente ? '<span class="tag warn">PENDENTE DE VALIDAÇÃO</span>' : ''} <span class="tag">${esc(d.content_type)}</span> ${d.recurring ? '<span class="tag pp">PADRÃO PROMOCIONAL RECORRENTE</span>' : ''}</h2>
   <div class="filters"><button onclick="openUrl(${jq(d.profile_url)})">ABRIR PERFIL</button><button ${d.video_url ? '' : 'disabled'} onclick="openUrl(${jq(d.video_url)})">ABRIR VÍDEO</button>
     <button class="ok" onclick="detailSt(${d.id},'CONFIRMADO')">CONFIRMAR</button><button class="bad" onclick="detailSt(${d.id},'DESCARTADO')">DESCARTAR</button>
     <button onclick="detailSt(${d.id},'REVISAR')">REVISAR</button><button onclick="detailSt(${d.id},'JÁ ENCAMINHADO')">JÁ ENCAMINHADO</button>
     <button onclick="reanalyze(${d.id})">REANALISAR (coleta de novo)</button><button onclick="expandId(${d.id})">ENCONTRAR PERFIS RELACIONADOS</button>
     <select onchange="detailSt(${d.id},this.value)">${STATUSES.map(s => `<option ${s === d.status ? 'selected' : ''}>${s}</option>`).join('')}</select></div>
   <div class="grid2"><div>${reasonsHtml(d)}
     <h3>Evidência principal</h3><div class="box">${esc(d.main_evidence || NI)}</div>
     <h3>Perfil</h3><div class="kv"><div>USERNAME</div><div>@${esc(d.username)}</div><div>URL_PERFIL</div><div>${esc(d.profile_url)}</div><div>NOME_EXIBIDO</div><div>${esc(d.display_name || NI)}</div>
      <div>BIO</div><div>${esc(d.bio || NI)}</div><div>LINK_BIO</div><div>${esc(d.bio_link || NI)}</div><div>PLATAFORMA</div><div>${esc((d.platforms || []).join(', ') || NI)}</div>
      <div>JOGO</div><div>${esc((d.games || []).join(', ') || NI)}</div><div>CÓDIGO PROMO</div><div>${esc((d.codes || []).join(', ') || NI)}</div><div>AFFILIATE_ID</div><div>${esc((d.affiliate_ids || []).join(', ') || NI)}</div>
      <div>HASHTAGS</div><div>${esc((d.hashtags || []).map(h => '#' + h).join(' ') || NI)}</div><div>FONTES</div><div>${esc((d.sources || []).join(', ') || NI)}</div>
      <div>STATUS PERFIL</div><div>${esc(d.profile_status)}</div><div>1ª COLETA</div><div>${esc(fmtDate(d.first_seen))}</div></div>
     <h3>Recursivo — transformar indicador em busca</h3><div>${d.indicators.map(x => `<button class="sm" onclick="searchInd(${jq(x.tipo)},${jq(x.valor)})">BUSCAR ${esc(x.tipo.toUpperCase())}: ${esc(clip(x.valor, 30))}</button> `).join('') || '<span class="mut">sem indicadores</span>'}</div>
     <h3>Clusters</h3><div>${d.clusters.map(c => `<button class="sm" onclick="closeModals();S.filters={cluster:${jq(c.tipo + ':' + c.valor)}};setView('results')">${esc(c.tipo)}: ${esc(c.valor)}</button> `).join('') || '<span class="mut">—</span>'}</div>
     <h3>Observação do analista</h3><textarea id="dNote" placeholder="OBSERVAÇÃO_ANALISTA (preservada nas exportações)">${esc(d.analyst_note)}</textarea>
     <button class="sm" onclick="saveNote(${d.id})">salvar observação</button></div>
    <div><h3>Links / cadeia de redirecionamento</h3>${linksHtml(d)}
     <h3>Evidências (${d.evidence_count})</h3>${d.evidences.map(e => `<div class="box ${e.kind === 'relacao' ? 'warn' : ''}"><span class="tag">${esc(e.kind === 'relacao' ? 'relação (não é evidência)' : e.kind)}</span>
       ${(e.tags || []).map(t => `<span class="tag pp">${esc(t)}</span>`).join('')}<span class="mut"> ${esc(e.data)} ${esc(e.hora)} ${esc(e.fuso)} · ${esc(e.source || NI)}</span>
       <div>${esc(e.caption || e.text || NI)}</div>
       ${e.url_video ? `<div><a href="${esc(safeUrl(e.url_video))}" target="_blank" rel="noopener">${esc(e.url_video)}</a></div>` : ''}
       ${e.source_url && e.source_url !== e.url_video ? `<div class="mut">fonte consultada: ${esc(e.source_url)}</div>` : ''}${e.query ? `<div class="mut">consulta: ${esc(e.query)}</div>` : ''}</div>`).join('') || '<div class="box warn">Nenhuma evidência registrada.</div>'}
     <h3>Adicionar evidência manual</h3>${manualForm(d.id)}
     <h3>Como foi encontrado</h3>${d.discoveries.map(x => `<div class="mut">${esc(x.source)} · “${esc(x.query)}” ${x.hunt ? '· ' + esc(x.hunt) : ''} · ${esc(fmtDate(x.found_at))}</div>`).join('')}</div></div>`;
  closeModals(); const m = modal(html); m.querySelector('.modal').dataset.cid = id;
}
function manualForm(id) {
  return `<div class="box"><textarea id="mText" placeholder="Cole aqui bio/legenda/texto visto no TikTok (URLs no texto são resolvidas)"></textarea>
   <div class="filters" style="margin-top:6px"><input id="mVid" placeholder="URL do vídeo" style="flex:1;max-width:none"><input id="mLink" placeholder="link externo" style="flex:1;max-width:none"></div>
   <div class="filters"><label class="i"><input type="checkbox" class="mTag" value="gameplay">vídeo mostra jogo/aposta</label><label class="i"><input type="checkbox" class="mTag" value="logomarca">logomarca de plataforma</label>
   <label class="i"><input type="checkbox" class="mTag" value="saque_demonstrado">saque/pagamento demonstrado</label>
   <button class="primary sm" onclick="saveManual(${id})">SALVAR EVIDÊNCIA</button></div></div>`;
}
async function saveManual(id) {
  try {
    const r = await api(`/api/candidates/${id}/evidence`, {json: {text: $('#mText').value, url_video: $('#mVid').value, link: $('#mLink').value, tags: [...document.querySelectorAll('.mTag:checked')].map(c => c.value)}});
    toast(r.novo ? 'Evidência registrada' : 'Evidência já existia'); showDetail(id); loadStats(); if (S.view !== 'export') loadResults?.();
  } catch (e) { toast('⚠ ' + e.message, 5000); }
}
async function saveNote(id) { await api(`/api/candidates/${id}/note`, {json: {note: $('#dNote').value}}); toast('Observação salva'); }
async function detailSt(id, st) { const note = $('#dNote') ? $('#dNote').value : undefined; try { await api(`/api/candidates/${id}/status`, {json: {status: st, note}}); toast(st); showDetail(id); loadStats(); if ($('#tb')) loadResults(); } catch (e) { toast('⚠ ' + e.message, 6000); } }
async function reanalyze(id) { toast('Coletando…'); const r = await api(`/api/candidates/${id}/reanalyze`, {method: 'POST', json: {}}); toast(r.profile_fetched ? 'Reanalisado' : `Perfil não coletado: ${r.profile_error || ''}`, 5000); showDetail(id); loadStats(); }

/* ------------------------------------------------------------ jobs */
async function trackJob(jobId, onDone) {
  const p = $('#jobPanel'); p.classList.remove('hidden');
  for (;;) {
    const j = await api(`/api/jobs/${jobId}`);
    p.innerHTML = `<b>${esc(j.label)}</b> <span class="mut">${j.status} · ${j.elapsed}s</span> <button class="sm" style="float:right" onclick="$('#jobPanel').classList.add('hidden')">✕</button>
      <div class="bar" style="margin-top:6px"><i style="width:${j.total ? j.done / j.total * 100 : (j.status === 'running' ? 8 : 100)}%"></i></div><pre>${esc(j.lines.join('\n'))}${j.error ? '\n⚠ ' + esc(j.error) : ''}</pre>`;
    if (j.status !== 'running') { loadStats(); if ($('#tb')) loadResults(); onDone && onDone(j); return j; }
    await new Promise(r => setTimeout(r, 900));
  }
}
async function searchInd(tipo, valor) {
  const r = await api('/api/search/indicator', {json: {tipo, valor}}); toast(`Buscando ${tipo}: ${valor}`);
  trackJob(r.job, j => j.result && toast(`${j.result.found} encontrados · ${j.result.new} novos · ${j.result.dups} já conhecidos` + (j.result.errors.length ? ` · ⚠ ${j.result.errors.length} fonte(s) indisponível(is)` : ''), 7000));
}

/* ------------------------------------------------------------ investigar / expandir */
function openInvestigate(prefill = '') {
  const m = modal(`<h2>INVESTIGAR PERFIL</h2><p class="mut">@username ou URL do TikTok (perfil ou vídeo). Extrai bio, links, vídeos, hashtags, menções, códigos, plataformas, domínios e jogos disponíveis publicamente; resolve a cadeia de links; pontua.</p>
    <div class="filters"><input id="invT" placeholder="@usuario ou https://www.tiktok.com/@usuario/video/…" style="flex:1;max-width:none" value="${esc(prefill)}"><button class="primary" id="invGo">INVESTIGAR</button></div><div id="invOut"></div>`, 'sm');
  $('#invT', m).focus(); $('#invT', m).onkeydown = e => e.key === 'Enter' && $('#invGo', m).click();
  $('#invGo', m).onclick = () => runInvestigate($('#invT', m).value, false, m);
}
async function runInvestigate(target, force, m) {
  const out = $('#invOut', m); out.innerHTML = '<div class="box">Coletando e analisando… (pode levar alguns segundos)</div>';
  try {
    const r = await api('/api/investigate', {json: {target, force}});
    if (r.cached) {
      const p = r.previous;
      out.innerHTML = `<div class="box warn"><b>ESTE ITEM JÁ FOI ANALISADO</b><div class="kv"><div>Data anterior</div><div>${esc(fmtDate(p.data_anterior))}</div><div>Status</div><div>${esc(p.status)}</div><div>Score anterior</div><div>${p.score} (${esc(p.classificacao)})</div><div>Evidência principal</div><div>${esc(p.evidencia_principal)}</div></div>
        <div class="mut">Evidências anteriores:</div>${p.evidencias.map(e => `<div>• [${esc(e.kind)}] ${esc(e.texto)} ${e.url ? esc(e.url) : ''}</div>`).join('')}
        <div style="margin-top:8px"><button class="primary" id="rean">REANALISAR AGORA</button> <button id="seeprev">VER EVIDÊNCIAS</button></div></div>`;
      $('#rean', out).onclick = () => runInvestigate(target, true, m); $('#seeprev', out).onclick = () => showDetail(r.id);
    } else if (!r.ok) out.innerHTML = `<div class="box bad">${esc(r.error)}</div>`;
    else {
      out.innerHTML = `<div class="box ${r.evidence_count ? 'ok' : 'warn'}"><b>Score ${r.score}</b> · ${esc(r.classification)} · ${r.evidence_count} evidência(s) · ${r.videos_collected} vídeo(s)
        ${r.profile_fetched ? '' : `<div class="neg">⚠ Perfil não coletado automaticamente: ${esc(r.profile_error || NI)}. Abra o perfil no TikTok, e registre o que viu em “+ EVIDÊNCIA MANUAL”. Enquanto isso, o item fica em PENDENTE DE VALIDAÇÃO.</div>`}
        <div style="margin-top:8px"><button onclick="showDetail(${r.id})">VER EVIDÊNCIAS</button> <button class="primary" onclick="expandId(${r.id})">ENCONTRAR PERFIS RELACIONADOS</button></div></div>`;
      loadStats(); if ($('#tb')) loadResults();
    }
  } catch (e) { out.innerHTML = `<div class="box bad">${esc(e.message)}</div>`; }
}
function openExpand() {
  const m = modal(`<h2>EXPANDIR PERFIL</h2><p class="mut">Informe um perfil-semente. A ferramenta investiga (se ainda não estiver na base) e gera novos candidatos: menções/perfis marcados, buscas por domínio, código, hashtag, plataforma e jogo. Não insere duplicidades.</p>
   <div class="filters"><input id="expT" placeholder="@usuario ou URL" style="flex:1;max-width:none"><button class="primary" id="expGo">ENCONTRAR PERFIS RELACIONADOS</button></div>`, 'sm');
  $('#expGo', m).onclick = async () => {
    const t = $('#expT', m).value; try { const r = await api('/api/expand', {json: {target: t}}); m.remove(); trackJob(r.job, showExpansion); } catch (e) { toast('⚠ ' + e.message, 6000); }
  };
}
async function expandId(id) { closeModals(); const r = await api('/api/expand', {json: {id}}); trackJob(r.job, showExpansion); }
function showExpansion(j) {
  const r = j.result; if (!r || !r.ok) return toast('Expansão falhou: ' + (j.error || (r && r.error) || ''));
  modal(`<h2>Expansão de @${esc(r.seed)}</h2>
   <div class="box ok"><b>${r.menções_novas.length}</b> perfis novos por menção/marcação · <b>${r.novos_por_busca}</b> novos por busca · <b>${r.relacionados_existentes.length}</b> relacionados já na base</div>
   ${r.menções_novas.length ? `<h3>Novos por menção (pendentes até haver evidência)</h3>${r.menções_novas.map(m => `<span class="tag">@${esc(m)}</span>`).join('')}` : ''}
   <h3>Já na base (mesmo domínio/código/plataforma/campanha)</h3>${r.relacionados_existentes.map(x => `<div>@${esc(x.username)} <span class="tag">${esc(x.tipo)}: ${esc(x.valor)}</span> score ${x.score} · ${esc(x.status)}</div>`).join('') || '<span class="mut">nenhum</span>'}
   <h3>Buscas executadas</h3>${r.buscas.map(b => `<div class="mut">${esc(b.tipo)} · ${esc(b.consulta)} [${esc(b.fonte)}] → ${b.encontrados} (${b.novos} novos) ${b.erro ? '⚠ ' + esc(b.erro) : ''}</div>`).join('') || '<span class="mut">nenhuma</span>'}
   <div style="margin-top:10px"><button onclick="closeModals();setView('results')">VER RESULTADOS</button> <button onclick="closeModals();setView('pending')">VER PENDENTES</button></div>`);
}

/* ------------------------------------------------------------ importar / manual / busca */
function openImport() {
  const m = modal(`<h2>IMPORTAR LISTA</h2><p class="mut">Um item por linha (TXT/CSV ou colar): <code>@username</code>, URL de perfil/vídeo do TikTok, domínio, <code>#hashtag</code>. Perfis importados entram como <b>PENDENTE DE VALIDAÇÃO</b> até haver evidência (use “coletar perfis” ou evidência manual).</p>
   <textarea id="impT" placeholder="@perfil1&#10;https://www.tiktok.com/@perfil2/video/123&#10;dominio-exemplo.com&#10;#slotpagando"></textarea>
   <div class="filters"><input type="file" id="impF" accept=".txt,.csv"><input id="impS" value="lista importada" title="fonte de descoberta" style="width:170px"></div>
   <div class="filters"><label class="i"><input type="checkbox" id="impD" checked>tratar domínios importados como plataformas de apostas</label><label class="i"><input type="checkbox" id="impFetch">coletar perfis agora (lento)</label></div>
   <button class="primary" id="impGo">IMPORTAR</button><div id="impOut"></div>`);
  $('#impGo', m).onclick = async () => {
    const fd = new FormData(); const f = $('#impF', m).files[0]; if (f) fd.append('file', f); fd.append('text', $('#impT', m).value);
    fd.append('source', $('#impS', m).value); fd.append('domains_as_bet', $('#impD', m).checked ? '1' : '0'); fd.append('fetch', $('#impFetch', m).checked ? '1' : '0');
    try {
      const r = await api('/api/import', {form: fd});
      $('#impOut', m).innerHTML = `<div class="box ok">Perfis novos: <b>${r.perfis_novos}</b> · duplicados: ${r.perfis_duplicados} · vídeos: ${r.videos} · domínios: ${r.dominios} · hashtags: ${r.hashtags} · inválidos: ${r.invalidos}</div>`;
      loadStats(); if ($('#tb')) loadResults(); if (r.job) trackJob(r.job);
    } catch (e) { toast('⚠ ' + e.message, 5000); }
  };
}
function openManual() {
  const m = modal(`<h2>+ EVIDÊNCIA MANUAL</h2><p class="mut">Fallback quando a coleta automática não é possível: registre o que você viu no TikTok. Cria o candidato se não existir.</p>
   <input id="mmU" placeholder="@username" style="width:100%;margin-bottom:6px">${manualForm(0)}`, 'sm');
  m.querySelector('button.primary.sm').onclick = async () => {
    try {
      const r = await api('/api/manual', {json: {username: $('#mmU', m).value, text: $('#mText', m).value, url_video: $('#mVid', m).value, link: $('#mLink', m).value, tags: [...m.querySelectorAll('.mTag:checked')].map(c => c.value)}});
      m.remove(); toast('Evidência registrada'); loadStats(); if ($('#tb')) loadResults(); showDetail(r.id);
    } catch (e) { toast('⚠ ' + e.message, 5000); }
  };
}
async function openSearch() {
  if (!S.settings) S.settings = await api('/api/settings');
  const st = S.settings;
  const srcBox = `<div class="filters">${SOURCES.map(([k, n]) => `<label class="i"><input type="checkbox" class="sSrc" value="${k}" ${k !== 'tiktok_tag' ? 'checked' : ''}>${n}</label>`).join('')}</div>`;
  const m = modal(`<h2>NOVA BUSCA</h2><p class="mut">Nenhuma palavra isolada é evidência: resultados são candidatos e só sobem de prioridade com sinais combinados. Fontes automáticas podem estar bloqueadas — veja o log e use os links manuais + importação.</p>
   <div class="filters"><button class="sm" data-t="free">Livre</button><button class="sm" data-t="combo">Combinar termos</button><button class="sm" data-t="games">Jogos (lista editável)</button><button class="sm" data-t="tags">Hashtags</button></div>
   <div id="sBody"></div>${srcBox}<button class="primary" id="sGo">EXECUTAR BUSCA</button> <button id="sMan">abrir buscas manuais (navegador)</button><div id="sOut"></div>`);
  let mode = 'free';
  const draw = () => {
    $('#sBody', m).innerHTML = mode === 'free' ? `<textarea id="sQ" placeholder="uma consulta por linha, ex.:&#10;&quot;link na bio&quot; saque&#10;&quot;plataforma pagando&quot; pix"></textarea>` :
      mode === 'combo' ? `<div class="grid2"><div><b>Termos A</b><textarea id="sA">${esc(['link na bio','cadastre-se','plataforma pagando','grupo vip','crie sua conta','horário pagante'].join('\n'))}</textarea></div><div><b>Termos B</b><textarea id="sB">${esc(['saque','bônus','pix','sinais','tigrinho','cupom'].join('\n'))}</textarea></div></div><div class="mut">Gera todas as combinações A × B.</div>` :
      mode === 'games' ? `<textarea id="sQ">${esc(st.games.map(g => g.split('|')[0] + ' link na bio').join('\n'))}</textarea><div class="mut">Gerado da lista de jogos (edite em CONFIG).</div>` :
      `<textarea id="sQ">${esc(st.hashtags.map(h => '#' + h).join('\n'))}</textarea><div class="mut">Hashtag é mecanismo de descoberta, nunca confirmação.</div>`;
  };
  m.querySelectorAll('[data-t]').forEach(b => b.onclick = () => { mode = b.dataset.t; draw(); }); draw();
  const payload = () => {
    const srcs = [...m.querySelectorAll('.sSrc:checked')].map(c => c.value);
    return mode === 'combo' ? {combine_a: lines($('#sA', m).value), combine_b: lines($('#sB', m).value), queries: [], sources: srcs} : {queries: lines($('#sQ', m).value), sources: srcs};
  };
  $('#sGo', m).onclick = async () => {
    try { const r = await api('/api/search', {json: payload()}); m.remove(); trackJob(r.job, j => j.result && toast(`Busca concluída: ${j.result.found} resultados · ${j.result.new} novos · ${j.result.dups} já conhecidos · ${j.result.errors} erro(s)`, 8000)); }
    catch (e) { toast('⚠ ' + e.message, 5000); }
  };
  $('#sMan', m).onclick = async () => {
    const q = (mode === 'combo' ? `"${lines($('#sA', m).value)[0]}" "${lines($('#sB', m).value)[0]}"` : lines($('#sQ', m).value)[0]) || '';
    const urls = await api('/api/manual-search-urls?q=' + encodeURIComponent(q));
    $('#sOut', m).innerHTML = `<div class="box"><b>Buscas manuais para “${esc(q)}”</b> — copie URLs de perfis/vídeos e use IMPORTAR LISTA.<br>${urls.map(u => `<a class="btn" href="${esc(u.url)}" target="_blank" rel="noopener">${esc(u.fonte)}</a> `).join('')}</div>`;
  };
}

/* ------------------------------------------------------------ clusters */
async function renderClusters() {
  $('#view').innerHTML = `<div class="filters"><h2 style="margin:0">CLUSTERS</h2><select id="cTipo"><option value="">todos os tipos</option><option value="DOMINIO">Domínio</option><option value="AGREGADOR">Agregador</option><option value="PLATAFORMA">Plataforma</option><option value="CODIGO">Código promocional</option><option value="CAMPANHA">Campanha</option><option value="AFILIADO_ID">ID de afiliado</option></select>
    mín. perfis <input id="cMin" type="number" value="2" min="1" style="width:60px"></div><p class="mut">Perfis diferentes que apontam para o mesmo destino NÃO são deduplicados: formam um cluster. Um domínio compartilhado por perfis promotores soma pontos no score.</p><div id="cl"></div>`;
  const load = async () => {
    const list = await api(`/api/clusters?min=${$('#cMin').value}&tipo=${$('#cTipo').value}`);
    $('#cl').innerHTML = list.map(c => `<div class="box"><div><span class="tag">${esc(c.tipo_label)}</span> <b style="font-size:15px">${esc(c.valor)}</b></div>
      <div><b>${c.perfis_qtd} perfis</b> · <span class="pos">${c.confirmados} confirmados</span> · <span style="color:var(--warn)">${c.revisar} revisar</span> · ${c.descartados} descartados</div>
      ${c.affiliate_ids.length ? `<div>Affiliate IDs: ${c.affiliate_ids.map(a => `<span class="tag pp">${esc(a)}</span>`).join('')}</div>` : ''}
      <div class="mut">${c.perfis.slice(0, 12).map(p => `@${esc(p.username)}${p.aff.length ? ' → ' + esc(p.aff.join(',')) : ''}`).join(' · ')}${c.perfis.length > 12 ? ' …' : ''}</div>
      ${c.links.length ? `<div class="mut clip" style="max-width:none">Links: ${c.links.slice(0, 3).map(esc).join(' · ')}</div>` : ''}
      <div style="margin-top:6px"><button class="sm" onclick="S.filters={cluster:${jq(c.cluster)}};setView('results')">VER PERFIS</button>
      ${c.tipo === 'DOMINIO' ? `<button class="sm" onclick="searchInd('domínio',${jq(c.valor)})">BUSCAR ESTE DOMÍNIO</button>` : ''}${c.tipo === 'PLATAFORMA' ? `<button class="sm" onclick="searchInd('plataforma',${jq(c.valor)})">BUSCAR PLATAFORMA</button>` : ''}${c.tipo === 'CODIGO' ? `<button class="sm" onclick="searchInd('código',${jq(c.valor)})">BUSCAR CÓDIGO</button>` : ''}
      <button class="sm" onclick="copyText(${jq(c.perfis.map(p => 'https://www.tiktok.com/@' + p.username).join('\n'))});toast('URLs copiadas')">COPIAR PERFIS</button></div></div>`).join('') || '<div class="mut">Nenhum cluster com esse mínimo de perfis.</div>';
  };
  $('#cMin').onchange = $('#cTipo').onchange = load; load();
}

/* ------------------------------------------------------------ caças */
async function renderHunts() {
  const hs = await api('/api/hunts');
  const desc = {queries: 'consultas editáveis', expand_confirmed: 'expande perfis com status CONFIRMADO', affiliate_links: 'consultas + IDs de afiliado já encontrados', known_domains: 'consultas + domínios já encontrados/cadastrados'};
  $('#view').innerHTML = `<div class="filters"><h2 style="margin:0">CAÇAS</h2><button class="primary" onclick="runHunts()">EXECUTAR HABILITADAS</button><button onclick="newHunt()">+ NOVA CAÇA</button></div>` +
    hs.map(h => `<div class="hunt ${h.enabled ? '' : 'off'}" data-id="${h.id}"><div class="filters"><label class="i"><input type="checkbox" class="hEn" ${h.enabled ? 'checked' : ''}><b>habilitada</b></label>
      <input class="hName grow" value="${esc(h.name)}" style="max-width:none"><span class="tag">${esc(desc[h.kind] || h.kind)}</span><span class="mut">última: ${esc(fmtDate(h.last_run))}</span></div>
      <textarea class="hQ" style="min-height:60px" placeholder="uma consulta por linha">${esc(h.queries.join('\n'))}</textarea>
      <div class="filters">${SOURCES.map(([k, n]) => `<label class="i"><input type="checkbox" class="hSrc" value="${k}" ${h.sources.includes(k) ? 'checked' : ''}>${n}</label>`).join('')}
      <button class="sm" onclick="saveHunt(${h.id},this)">SALVAR</button><button class="sm primary" onclick="runHunts([${h.id}])">EXECUTAR</button><button class="sm bad" onclick="delHunt(${h.id})">excluir</button></div></div>`).join('');
}
async function saveHunt(id, btn) {
  const el = btn.closest('.hunt');
  await api(`/api/hunts/${id}`, {method: 'PUT', json: {name: $('.hName', el).value, queries: lines($('.hQ', el).value), sources: [...el.querySelectorAll('.hSrc:checked')].map(c => c.value), enabled: $('.hEn', el).checked}});
  toast('Caça salva'); renderHunts();
}
async function delHunt(id) { if (confirm('Excluir esta caça?')) { await api(`/api/hunts/${id}`, {method: 'DELETE'}); renderHunts(); } }
async function newHunt() { const n = prompt('Nome da caça'); if (n) { await api('/api/hunts', {json: {name: n, queries: []}}); renderHunts(); } }
async function runHunts(ids) {
  const r = await api('/api/hunts/run', {json: {ids}}); trackJob(r.job, j => j.result && toast(`Caças concluídas: ${j.result.reduce((a, x) => a + (x.new || 0), 0)} novos candidatos`, 8000));
}

/* ------------------------------------------------------------ missão / métricas / log */
async function renderMission() {
  const m = (await api('/api/stats')).mission;
  $('#view').innerHTML = `<div class="mission"><h2>MODO MISSÃO</h2><div class="filters">META: <input id="goal" type="number" value="${m.meta}" style="width:90px"><button onclick="saveGoal()">salvar meta</button></div>
   <div class="bar" style="height:16px"><i style="width:${m.progresso * 100}%"></i></div>
   <div class="row"><div><div class="num">${m.meta}</div><div class="mut">META</div></div><div><div class="num pos">${m.confirmados}</div><div class="mut">CONFIRMADOS</div></div>
   <div><div class="num">${m.restantes}</div><div class="mut">RESTANTES</div></div><div><div class="num" style="color:var(--warn)">${m.em_revisao}</div><div class="mut">CANDIDATOS EM REVISÃO</div></div>
   <div><div class="num">${m.total_coletado}</div><div class="mut">TOTAL COLETADO</div></div><div><div class="num">${m.pendentes}</div><div class="mut">PENDENTES DE VALIDAÇÃO</div></div></div>
   <div class="box">${m.taxa_confirmacao != null ? `Taxa de confirmação observada: <b>${(m.taxa_confirmacao * 100).toFixed(0)}%</b>. ` : 'Taxa de confirmação: dados insuficientes (confirme/descarte ao menos 5 itens). '}
   ${m.restantes ? `Para fechar a meta, estime coletar ≈ <b>${m.candidatos_adicionais_estimados}</b> candidatos adicionais além dos ${m.em_revisao} em revisão.` : '<b>Meta atingida.</b>'} A coleta não para ao chegar em ${m.meta}: colete além para compensar falsos positivos, duplicidades e perfis indisponíveis.</div>
   <div class="filters"><button class="review" onclick="startReview()">▶ MODO REVISÃO RÁPIDA</button><button onclick="runHunts()">EXECUTAR CAÇAS HABILITADAS</button></div>
   <p class="mut">Acompanhamento operacional apenas.</p></div>`;
}
async function saveGoal() { await api('/api/settings', {method: 'PUT', json: {goal: +$('#goal').value}}); renderMission(); loadStats(); }
async function renderMetrics() {
  const m = await api('/api/metrics'); const pct = x => x == null ? '—' : (x * 100).toFixed(0) + '%';
  const tbl = (rows, cols) => `<table><thead><tr>${cols.map(c => `<th>${c[0]}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${cols.map(c => `<td>${esc(c[1](r))}</td>`).join('')}</tr>`).join('') || '<tr><td class="mut">sem dados</td></tr>'}</tbody></table>`;
  $('#view').innerHTML = `<h2>MÉTRICAS</h2><div class="box warn">${esc(m.aviso)}</div><div id="panel2" style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px">
   <div class="card"><div class="n">${pct(m.taxa_falso_positivo)}</div><div class="l">taxa de falso positivo (descartes manuais)</div></div><div class="card"><div class="n">${pct(m.taxa_confirmacao)}</div><div class="l">taxa de confirmação</div></div>
   <div class="card"><div class="n">${m.confirmados}</div><div class="l">confirmados</div></div><div class="card"><div class="n">${m.decididos}</div><div class="l">decididos pelo analista</div></div></div>
   <div class="grid2"><div><h3>Melhores consultas</h3>${tbl(m.melhores_consultas, [['consulta', r => r.valor], ['achados', r => r.total], ['confirm.', r => r.confirmados], ['descart.', r => r.descartados]])}
   <h3>Fontes de descoberta</h3>${tbl(m.fontes, [['fonte', r => r.valor], ['achados', r => r.total], ['confirm.', r => r.confirmados], ['descart.', r => r.descartados]])}</div>
   <div><h3>Melhores hashtags</h3>${tbl(m.melhores_hashtags, [['hashtag', r => '#' + r.valor], ['perfis', r => r.qtd]])}<h3>Domínios mais encontrados</h3>${tbl(m.dominios, [['domínio', r => r.valor], ['perfis', r => r.qtd]])}
   <h3>Plataformas mais encontradas</h3>${tbl(m.plataformas, [['plataforma', r => r.valor], ['perfis', r => r.qtd]])}<h3>Principais jogos</h3>${tbl(m.jogos, [['jogo', r => r.valor], ['perfis', r => r.qtd]])}</div></div>`;
}
async function renderLog() {
  const l = await api('/api/log');
  $('#view').innerHTML = `<h2>LOG DE PESQUISAS</h2><div class="tblwrap"><table><thead><tr><th>Horário</th><th>Caça/origem</th><th>Consulta</th><th>Fonte</th><th>Encontrados</th><th>Novos</th><th>Duplicados</th><th>Erros</th><th>Tempo</th></tr></thead><tbody>${l.map(r => `<tr><td>${esc(fmtDate(r.ts))}</td><td>${esc(r.hunt)}</td><td>${esc(r.query)}</td><td>${esc(r.source)}</td><td>${r.found}</td><td>${r.new}</td><td>${r.dups}</td><td class="${r.errors ? 'neg' : ''}">${r.errors ? '⚠ ' + esc(r.error_msg) : '0'}</td><td>${r.duration_ms} ms</td></tr>`).join('') || '<tr><td colspan="9" class="mut">Nenhuma pesquisa executada.</td></tr>'}</tbody></table></div>`;
}

/* ------------------------------------------------------------ config */
const WLABEL = {link_bet:'link externo (aposta)',cta:'CTA explícito',gameplay:'vídeo de jogo',platform:'nome/logo plataforma',payment:'saque/pagamento',aff_link:'parâmetro de afiliado',bonus_code:'bônus/cupom/código',group:'grupo Telegram/WhatsApp',shared_domain:'domínio compartilhado',hashtag:'hashtag relacionada',expressions:'expressões (horário pagante…)',recurrence:'recorrência',padrao_recorrente:'padrão promocional recorrente',r_journalism:'redutor jornalismo',r_critica:'redutor crítica',r_institutional:'redutor institucional',r_legal:'redutor jurídico',r_educ:'redutor educativo/prevenção',r_legislation:'redutor legislação',r_comment:'redutor comentário',r_incidental:'redutor uso incidental'};
async function renderConfig() {
  const s = S.settings = await api('/api/settings');
  const ta = (id, arr, h) => `<h3>${h}</h3><textarea id="${id}" style="min-height:110px">${esc(arr.join('\n'))}</textarea>`;
  $('#view').innerHTML = `<h2>CONFIGURAÇÕES</h2><p class="mut">Tudo é editável sem mexer no código. Para aplicar novos pesos/limites aos itens já coletados, use “SALVAR E REANALISAR TUDO” (status definidos manualmente são preservados).</p>
   <h3>Limites de classificação</h3><div class="filters">ALTA PROBABILIDADE ≥ <input id="tAlta" type="number" value="${s.thresholds.alta}" style="width:70px"> REVISAR ≥ <input id="tRev" type="number" value="${s.thresholds.revisar}" style="width:70px"> (abaixo: BAIXA RELEVÂNCIA)</div>
   <h3>Pesos do score</h3><div class="wgrid">${Object.keys(s.weights).map(k => `<label>${WLABEL[k] || k}<input type="number" data-w="${k}" value="${s.weights[k]}"></label>`).join('')}</div>
   <h3>Operação</h3><div class="filters">Meta <input id="cGoal" type="number" value="${s.goal}" style="width:80px"> Cache (dias) <input id="cCache" type="number" value="${s.cache_days}" style="width:60px"> Intervalo entre consultas (s) <input id="cDelay" type="number" step="0.1" value="${s.request_delay}" style="width:70px"> Máx. perfis enriquecidos por busca <input id="cEnr" type="number" value="${s.enrich_max}" style="width:70px"></div>
   <div class="filters"><label class="i"><input type="checkbox" id="cAuto" ${s.auto_discard_low ? 'checked' : ''}>marcar BAIXA RELEVÂNCIA como DESCARTADO automaticamente (nunca apaga)</label><label class="i"><input type="checkbox" id="cRes" ${s.resolve_links ? 'checked' : ''}>resolver redirecionamentos de links</label><label class="i"><input type="checkbox" id="cEnrich" ${s.enrich_after_search ? 'checked' : ''}>coletar perfil dos novos após buscas</label></div>
   <div class="grid2"><div>${ta('cGames', s.games, 'Jogos (um por linha; apelidos com “|”)')}${ta('cHash', s.hashtags, 'Hashtags de descoberta')}</div>
   <div>${ta('cPlat', s.platforms, 'Plataformas (Nome|dominio1,dominio2)')}${ta('cDom', s.bet_domains, 'Domínios de apostas conhecidos')}</div></div>
   <div class="grid2"><div>${ta('cAgg', s.aggregators, 'Agregadores de links')}</div><div>${ta('cGen', s.generic_games, 'Termos de jogo genéricos (não bastam p/ “gameplay”)')}</div></div>
   <h3>Léxicos avançados (JSON — sobrescreve listas padrão por chave: cta, payment, bonus, group, expressions, bet_terms, fp_*)</h3><textarea id="cLex" style="min-height:70px">${esc(JSON.stringify(s.lexicons || {}, null, 1))}</textarea>
   <div class="filters" style="margin-top:10px"><button class="primary" onclick="saveConfig(false)">SALVAR</button><button onclick="saveConfig(true)">SALVAR E REANALISAR TUDO</button></div>`;
}
async function saveConfig(re) {
  const w = {}; document.querySelectorAll('[data-w]').forEach(i => w[i.dataset.w] = +i.value);
  let lex; try { lex = JSON.parse($('#cLex').value || '{}'); } catch (e) { return toast('JSON dos léxicos inválido'); }
  const body = {weights: w, thresholds: {alta: +$('#tAlta').value, revisar: +$('#tRev').value}, goal: +$('#cGoal').value, cache_days: +$('#cCache').value,
    request_delay: +$('#cDelay').value, enrich_max: +$('#cEnr').value, auto_discard_low: $('#cAuto').checked, resolve_links: $('#cRes').checked, enrich_after_search: $('#cEnrich').checked,
    games: lines($('#cGames').value), hashtags: lines($('#cHash').value).map(h => h.replace(/^#/, '')), platforms: lines($('#cPlat').value), bet_domains: lines($('#cDom').value),
    aggregators: lines($('#cAgg').value), generic_games: lines($('#cGen').value), lexicons: lex};
  const r = await api('/api/settings' + (re ? '?reanalyze=1' : ''), {method: 'PUT', json: body}); S.settings = r.settings;
  toast(re ? `Salvo. ${r.reanalisados} candidatos reanalisados.` : 'Configurações salvas'); loadStats();
}

/* ------------------------------------------------------------ exportar */
function renderExport() {
  const nf = Object.values(S.filters).filter(Boolean).length;
  $('#view').innerHTML = `<h2>EXPORTAR</h2><div class="box">Escopo: <select id="xScope"><option value="confirmed">somente CONFIRMADOS (e JÁ ENCAMINHADOS)</option><option value="filtered">resultados filtrados na aba RESULTADOS (${nf} filtro(s))</option><option value="all">tudo (inclui pendentes/descartados)</option></select>
   <div class="mut" style="margin-top:4px">Itens sem evidência (PENDENTE DE VALIDAÇÃO) não podem ser confirmados e, portanto, não entram no escopo “confirmados”.</div></div>
   <div class="grid2"><div class="box"><h3>CSV simplificado (encaminhamento)</h3><div class="mut">username, url_perfil, url_video, evidencia, dominio, url_externa, score, status, data_coleta, observacao_analista</div><button class="primary" onclick="dl('csv','simple')">BAIXAR CSV</button> <button onclick="dl('xlsx','simple')">XLSX</button></div>
   <div class="box"><h3>Exportação COMPLETA</h3><div class="mut">todos os campos coletados (bio, legenda, cadeia de URL, parâmetros, motivos do score, fonte, fuso…)</div><button class="primary" onclick="dl('csv','full')">CSV COMPLETO</button> <button onclick="dl('xlsx','full')">XLSX</button> <button onclick="dl('json','full')">JSON</button></div></div>
   <h3>Copiar</h3><div class="filters"><button onclick="cp('profiles')">COPIAR PERFIS CONFIRMADOS</button><button onclick="cp('usernames')">COPIAR USERNAMES</button><button onclick="cp('videos')">COPIAR VÍDEOS</button></div><textarea id="cpOut" placeholder="a lista copiada aparece aqui" style="min-height:160px"></textarea>`;
}
function xq(extra) { const sc = $('#xScope').value; const f = sc === 'filtered' ? {...S.filters, view: 'results'} : {}; return Object.entries({...f, ...extra}).filter(([, v]) => v).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&'); }
function dl(fmt, kind) { const sc = $('#xScope').value; window.location = `/api/export?${xq({format: fmt, kind, scope: sc === 'filtered' ? 'all' : sc})}`; }
async function cp(what) { const sc = $('#xScope').value; const t = await api(`/api/copy?${xq({what, scope: sc === 'filtered' ? 'all' : sc})}`); $('#cpOut').value = t; await copyText(t); toast(`${t ? t.split('\n').length : 0} itens copiados`); }

/* ------------------------------------------------------------ MODO REVISÃO RÁPIDA */
const R = {ids: [], i: 0, done: 0};
async function startReview() {
  const q = await api('/api/queue?n=200'); if (!q.ids.length) return toast('Nada na fila de revisão (NOVO/REVISAR com evidência).');
  R.ids = q.ids; R.i = 0; R.done = 0; R.total = q.total;
  const ov = document.createElement('div'); ov.className = 'review-ov'; document.body.appendChild(ov); reviewShow();
  document.addEventListener('keydown', reviewKey);
}
function endReview() { document.removeEventListener('keydown', reviewKey); $('.review-ov')?.remove(); loadStats(); if ($('#tb')) loadResults(); }
async function reviewShow() {
  const ov = $('.review-ov'); if (!ov) return;
  if (R.i >= R.ids.length) { ov.innerHTML = `<h2>Fila concluída ✔</h2><p>${R.done} itens decididos nesta sessão.</p><button onclick="endReview()">FECHAR</button>`; return; }
  const d = await api(`/api/candidates/${R.ids[R.i]}`);
  const evs = d.evidences.filter(e => e.kind !== 'relacao').slice(0, 6);
  ov.innerHTML = `<div class="filters"><b>REVISÃO RÁPIDA</b><span class="mut">${R.i + 1}/${R.ids.length} · decididos ${R.done}</span><button class="sm" onclick="endReview()">sair (Esc)</button></div>
   <div class="filters"><span class="bigscore ${d.score >= 70 ? 'neg' : ''}">${d.score}</span><div><h2 style="margin:0">@${esc(d.username)} <span class="tag">${esc(d.content_type)}</span> ${d.recurring ? '<span class="tag pp">PADRÃO RECORRENTE</span>' : ''}</h2><div class="mut">${esc(d.classification)} · ${esc(d.display_name || '')}</div></div></div>
   <div class="big"><button class="ok" onclick="reviewAct('CONFIRMADO')">CONFIRMAR <kbd>C</kbd></button><button class="bad" onclick="reviewAct('DESCARTADO')">DESCARTAR <kbd>D</kbd></button><button onclick="reviewAct(null)">PULAR <kbd>S</kbd></button>
    <button onclick="openUrl(${jq(d.profile_url)})">PERFIL <kbd>O</kbd></button><button ${d.video_url ? '' : 'disabled'} onclick="openUrl(${jq(d.video_url)})">VÍDEO <kbd>V</kbd></button><button ${d.link_final ? '' : 'disabled'} onclick="openUrl(${jq(d.link_final)})">LINK <kbd>L</kbd></button></div>
   <div class="grid2"><div>${reasonsHtml(d)}<div class="box"><b>Evidência principal</b><br>${esc(d.main_evidence)}</div>
     <div class="box"><b>BIO</b> ${esc(d.bio || NI)}<br><b>Plataforma</b> ${esc((d.platforms || []).join(', ') || NI)} · <b>Affiliate</b> ${esc((d.affiliate_ids || []).join(', ') || NI)} · <b>Código</b> ${esc((d.codes || []).join(', ') || NI)}</div>
     <textarea id="rNote" placeholder="observação do analista (opcional)">${esc(d.analyst_note)}</textarea></div>
    <div>${linksHtml(d)}<h3>Evidências</h3>${evs.map(e => `<div class="box"><span class="tag">${esc(e.kind)}</span> <span class="mut">${esc(e.data)} ${esc(e.hora)} · ${esc(e.source)}</span><div>${esc(clip(e.caption || e.text, 400))}</div>${e.url_video ? `<a href="${esc(safeUrl(e.url_video))}" target="_blank" rel="noopener">${esc(e.url_video)}</a>` : ''}</div>`).join('')}</div></div>`;
  R.cur = d;
}
async function reviewAct(st) {
  if (st) { try { await api(`/api/candidates/${R.cur.id}/status`, {json: {status: st, note: $('#rNote')?.value}}); R.done++; } catch (e) { return toast('⚠ ' + e.message, 5000); } }
  R.i++; reviewShow();
}
function reviewKey(e) {
  if (!$('.review-ov')) return; if (document.activeElement.tagName === 'TEXTAREA') { if (e.key === 'Escape') document.activeElement.blur(); return; }
  const k = e.key.toLowerCase();
  if (k === 'c') reviewAct('CONFIRMADO'); else if (k === 'd') reviewAct('DESCARTADO'); else if (k === 's') reviewAct(null);
  else if (k === 'o') openUrl(R.cur?.profile_url); else if (k === 'v') openUrl(R.cur?.video_url); else if (k === 'l') openUrl(R.cur?.link_final);
  else if (k === 'escape') endReview();
}

setView('results');
