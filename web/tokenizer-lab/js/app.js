// Tokenizer Lab – hlavní vlákno: UI, záložky, vykreslování. Těžká práce běží ve Workerech.
import { BpeTokenizer, toHfJson, fromHfJson, toCsv } from './bpe.js';
import { ALPHABET, EOT, tokenBytes, charCount } from './bytelevel.js';
import { LineChart, BarChart } from './charts.js';
import { SAMPLE_DOCS, SAMPLE_TEXT, DEMO_SENTENCE } from './sample.js';

const $ = id => document.getElementById(id);
const nf = new Intl.NumberFormat('cs-CZ');
const nf1 = new Intl.NumberFormat('cs-CZ', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat('cs-CZ', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const MAX_TOTAL = 200 * 1048576;
const ALLOWED = /\.(txt|md|jsonl|ndjson|csv)$/i;
const strictUtf8 = new TextDecoder('utf-8', { fatal: true });
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* bez úložiště */ } },
};
const esc = s => s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmtSize = b => b < 1024 ? `${b} B` : b < 1048576 ? `${nf1.format(b / 1024)} kB` : `${nf1.format(b / 1048576)} MB`;

// ---------------------------------------------------------------- stav
const S = {
  files: [],          // {id, file, status, frac, chars, words, docs, sample, warn}
  useSample: false,
  nextId: 1,
  train: null,        // {idToToken, merges:[[a,b,id,count]], state, info, history, vocabSize}
  trainedTok: null, trainedTokAt: -1,
  loaded: null,       // {name, data, tok}
};

const dataWorker = new Worker(new URL('./data.worker.js', import.meta.url), { type: 'module' });
const trainWorker = new Worker(new URL('./train.worker.js', import.meta.url), { type: 'module' });

// ---------------------------------------------------------------- záložky, motiv
const tabs = [...document.querySelectorAll('[role="tab"]')];
function selectTab(tab, focus = false) {
  for (const t of tabs) {
    const on = t === tab;
    t.setAttribute('aria-selected', on);
    t.tabIndex = on ? 0 : -1;
    $(t.getAttribute('aria-controls')).hidden = !on;
  }
  if (focus) tab.focus();
  store.set('tab', tab.id);
  if (tab.id === 'tab-view') renderView();
  if (tab.id === 'tab-train') { cptChart.draw(); histChart.draw(); }
}
tabs.forEach((t, i) => {
  t.addEventListener('click', () => selectTab(t));
  t.addEventListener('keydown', e => {
    let j = null;
    if (e.key === 'ArrowRight') j = (i + 1) % tabs.length;
    if (e.key === 'ArrowLeft') j = (i - 1 + tabs.length) % tabs.length;
    if (e.key === 'Home') j = 0;
    if (e.key === 'End') j = tabs.length - 1;
    if (j !== null) { e.preventDefault(); selectTab(tabs[j], true); }
  });
});

const THEMES = ['system', 'light', 'dark'];
const THEME_LABEL = { system: 'systém', light: 'světlý', dark: 'tmavý' };
function applyTheme(t) {
  if (t === 'system') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', t);
  $('themeBtn').textContent = `Motiv: ${THEME_LABEL[t]}`;
  store.set('theme', t);
  requestAnimationFrame(() => { cptChart?.draw(); histChart?.draw(); });
}
$('themeBtn').addEventListener('click', () => {
  const cur = store.get('theme') || 'system';
  applyTheme(THEMES[(THEMES.indexOf(cur) + 1) % 3]);
});
$('presentBtn').addEventListener('click', e => {
  const on = document.documentElement.classList.toggle('present');
  e.currentTarget.setAttribute('aria-pressed', on);
});

// ---------------------------------------------------------------- 1) data
const drop = $('drop');
['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, () => drop.classList.remove('over')));
drop.addEventListener('drop', e => { e.preventDefault(); addFiles([...e.dataTransfer.files]); });
$('fileInput').addEventListener('change', e => { addFiles([...e.target.files]); e.target.value = ''; });
$('sampleBtn').addEventListener('click', () => {
  S.useSample = true;
  renderFiles();
  refreshSources();
});
$('fieldInput').addEventListener('change', () => {
  // znovu spočítat JSONL/CSV s novým polem
  for (const f of S.files) if (/\.(jsonl|ndjson|csv)$/i.test(f.file.name)) { f.status = 'queued'; queueScan(f); }
  renderFiles();
});
$('toTrainBtn').addEventListener('click', () => selectTab($('tab-train'), true));

function totalBytes() { return S.files.reduce((s, f) => s + f.file.size, 0); }

function addFiles(list) {
  const warns = [];
  for (const file of list) {
    if (!ALLOWED.test(file.name)) { warns.push(`„${file.name}“ přeskočen – podporované jsou .txt, .md, .jsonl, .csv.`); continue; }
    if (totalBytes() + file.size > MAX_TOTAL) {
      warns.push(`„${file.name}“ (${fmtSize(file.size)}) přeskočen – celkový limit je 200 MB.`); continue;
    }
    if (S.files.some(f => f.file.name === file.name && f.file.size === file.size)) continue;
    const f = { id: S.nextId++, file, status: 'queued', frac: 0 };
    if (file.size > 50 * 1048576) f.warn = 'velký soubor – čte se po částech';
    S.files.push(f);
    queueScan(f);
  }
  showWarn(warns);
  renderFiles();
}

function showWarn(list) {
  const el = $('dataWarn');
  el.hidden = !list.length;
  el.innerHTML = list.map(esc).join('<br>');
}

const scanQueue = [];
let scanning = null;
function queueScan(f) { if (!scanQueue.includes(f)) scanQueue.push(f); pumpScan(); }
function pumpScan() {
  if (scanning || !scanQueue.length) return;
  scanning = scanQueue.shift();
  if (!S.files.includes(scanning)) { scanning = null; pumpScan(); return; }
  scanning.status = 'scanning';
  dataWorker.postMessage({ type: 'scan', id: scanning.id, file: scanning.file, field: $('fieldInput').value.trim() || 'text' });
  renderFiles();
}

let filesRaf = 0;
function renderFilesSoon() { if (!filesRaf) filesRaf = requestAnimationFrame(() => { filesRaf = 0; renderFiles(); }); }

function renderFiles() {
  const rows = [];
  if (S.useSample) {
    rows.push(`<tr><td><b>Ukázková data</b> <span class="muted small">(vestavěná)</span></td>
      <td class="num">${fmtSize(new TextEncoder().encode(SAMPLE_TEXT).length)}</td>
      <td class="num">${nf.format(charCount(SAMPLE_TEXT))}</td>
      <td class="num">${nf.format((SAMPLE_TEXT.match(/\S+/g) || []).length)}</td>
      <td class="num">${SAMPLE_DOCS.length}</td><td class="status-ok">připraveno</td>
      <td><button class="icon-btn" data-remove="sample" aria-label="Odebrat ukázková data">✕</button></td></tr>`);
  }
  for (const f of S.files) {
    let st;
    if (f.status === 'queued') st = 'čeká…';
    else if (f.status === 'scanning') st = `čtu ${Math.round((f.frac || 0) * 100)} %`;
    else if (f.status === 'error') st = `<span class="status-bad">chyba: ${esc(f.error || '')}</span>`;
    else {
      const notes = [];
      if (f.badLines) notes.push(`${nf.format(f.badLines)} vadných řádků`);
      if (f.missingField) notes.push(`${nf.format(f.missingField)}× chybí pole`);
      if (f.csvColumn === null && /\.csv$/i.test(f.file.name)) notes.push('sloupec nenalezen – beru celé řádky');
      if (f.warn) notes.push(f.warn);
      st = `<span class="status-ok">✓ připraveno</span>${notes.length ? `<br><span class="muted small">${esc(notes.join(', '))}</span>` : ''}`;
    }
    const done = f.status === 'ok';
    rows.push(`<tr><td>${esc(f.file.name)}</td><td class="num">${fmtSize(f.file.size)}</td>
      <td class="num">${done ? nf.format(f.chars) : '…'}</td><td class="num">${done ? nf.format(f.words) : '…'}</td>
      <td class="num">${done ? nf.format(f.docs) : '…'}</td><td>${st}</td>
      <td><button class="icon-btn" data-remove="${f.id}" aria-label="Odebrat ${esc(f.file.name)}">✕</button></td></tr>`);
  }
  $('fileRows').innerHTML = rows.length ? rows.join('') : '<tr class="empty"><td colspan="7">Nahrajte soubory nebo použijte ukázková data.</td></tr>';
  const ok = S.files.filter(f => f.status === 'ok');
  const tb = totalBytes();
  $('dataTotals').textContent = S.files.length || S.useSample
    ? `${S.files.length} souborů · ${fmtSize(tb)} z 200 MB · ${nf.format(ok.reduce((s, f) => s + f.chars, 0))} znaků${S.useSample ? ' · + ukázka' : ''}`
    : 'Zatím nic nenahráno.';
  $('toTrainBtn').disabled = !(ok.length || S.useSample);
}

$('fileRows').addEventListener('click', e => {
  const b = e.target.closest('[data-remove]');
  if (!b) return;
  if (b.dataset.remove === 'sample') S.useSample = false;
  else S.files = S.files.filter(f => String(f.id) !== b.dataset.remove);
  renderFiles(); refreshSources();
});

dataWorker.onmessage = e => {
  const m = e.data;
  if (m.type === 'scanProgress' || m.type === 'scanned' || (m.type === 'error' && m.id)) {
    const f = S.files.find(x => x.id === m.id);
    if (f) {
      if (m.type === 'scanProgress') f.frac = m.frac;
      else if (m.type === 'scanned') Object.assign(f, { status: 'ok', chars: m.chars, words: m.words, docs: m.docs, sample: m.sample, badLines: m.badLines, missingField: m.missingField, csvColumn: m.csvColumn });
      else { f.status = 'error'; f.error = m.message; }
    }
    if (m.type !== 'scanProgress') { scanning = null; pumpScan(); refreshSources(); }
    renderFilesSoon();
  } else if (m.type === 'pipeProgress') pipeProgress(m);
  else if (m.type === 'pipeDone') pipeDone(m);
  else if (m.type === 'error') { $('pipeMsg').textContent = `Chyba: ${m.message}`; $('pipeBtn').disabled = false; }
};

// ---------------------------------------------------------------- vykreslení tokenů
const PAL = 8;
/** Text tokenu pro zobrazení; neúplné UTF-8 se ukáže jako bajty ‹c4›. */
function tokenText(tok, id) {
  const t = tok.idToToken[id];
  if (tok.specialTokens.includes(t)) return { text: t, special: true };
  const bytes = tokenBytes(t);
  try { return { text: strictUtf8.decode(new Uint8Array(bytes)), bytes }; }
  catch { return { text: bytes.map(b => `‹${b.toString(16).padStart(2, '0')}›`).join(''), bytes, partial: true }; }
}

function tokenInner(tt, showSpaces) {
  if (tt.special || tt.partial) return esc(tt.text);
  let h = esc(tt.text);
  if (showSpaces) h = h.replace(/ /g, '<span class="sp">·</span>').replace(/\t/g, '<span class="sp">→</span>');
  return h;
}

/**
 * @param {HTMLElement} el
 * @param {BpeTokenizer} tok
 * @param {number[]} ids
 * @param {{words?:number[], showSpaces?:boolean, cls?:(i:number)=>string, limit?:number, focusable?:boolean}} o
 */
function renderTokens(el, tok, ids, o = {}) {
  const limit = o.limit ?? 20000;
  const parts = [];
  const n = Math.min(ids.length, limit);
  for (let i = 0; i < n; i++) {
    const id = ids[i];
    const tt = tokenText(tok, id);
    let cls = tt.special ? 'tk eot' : `tk c${id % PAL}`;
    if (!tt.special && tt.text.endsWith('\n')) cls += ' nl';
    if (o.words && i > 0 && o.words[i] !== o.words[i - 1]) cls += ' wstart';
    if (o.cls) cls += o.cls(i);
    parts.push(`<span class="${cls}" data-id="${id}"${o.focusable ? ' tabindex="0"' : ''}>${tokenInner(tt, o.showSpaces ?? true)}</span>`);
  }
  if (ids.length > limit) parts.push(`<span class="muted"> … a dalších ${nf.format(ids.length - limit)} tokenů</span>`);
  el.innerHTML = parts.join('');
  el._tok = tok;
}

// popisek tokenu při najetí / fokusu
const tip = $('tokTip');
function showTokTip(span) {
  const host = span.closest('.tokens, #compareRows');
  const tok = host?._tok;
  if (!tok) return;
  const id = +span.dataset.id;
  const t = tok.idToToken[id];
  const bytes = tok.bytesOf(id);
  const tt = tokenText(tok, id);
  const rank = tok.rankOf ? tok.rankOf(id) : undefined;
  tip.innerHTML = `<b>„${esc(tt.text)}“</b><br>
    <span>ID</span> ${id}${rank !== undefined ? ` · <span>spojení č.</span> ${nf.format(rank + 1)}` : id > tok.specialTokens.length && id <= tok.specialTokens.length + 256 ? ' · <span>základní bajt</span>' : ''}<br>
    <span>bajty</span> ${bytes.map(b => b.toString(16).padStart(2, '0')).join(' ')} (${bytes.length})<br>
    <span>v tokenizer.json</span> ${esc(t)}`;
  tip.hidden = false;
  const r = span.getBoundingClientRect();
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  let x = Math.min(Math.max(8, r.left), innerWidth - tw - 8);
  let y = r.top - th - 8;
  if (y < 8) y = r.bottom + 8;
  tip.style.left = x + 'px'; tip.style.top = y + 'px';
}
document.addEventListener('pointerover', e => { const s = e.target.closest?.('.tk[data-id]'); if (s) showTokTip(s); else tip.hidden = true; });
document.addEventListener('focusin', e => { const s = e.target.closest?.('.tk[data-id]'); if (s) showTokTip(s); else tip.hidden = true; });
document.addEventListener('scroll', () => { tip.hidden = true; }, true);

function attachRank(tok) {
  // ID → pořadí spojení, které token vytvořilo
  const m = new Map();
  tok.merges.forEach(([a, b], r) => { const id = tok.tokenToId.get(a + b); if (id !== undefined && !m.has(id)) m.set(id, r); });
  tok.rankOf = id => m.get(id);
  return tok;
}

// ---------------------------------------------------------------- 2) trénink
const cptChart = new LineChart($('cptChart'), { label: 'Znaků na token', empty: 'Spusťte trénink' });
const HIST_LABELS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12+'];
const histChart = new BarChart($('histChart'), { label: 'Délky tokenů', labels: HIST_LABELS, unit: l => `délka ${l} B` });

$('stepSentence').value = DEMO_SENTENCE;

function trainSource() {
  const ok = S.files.filter(f => f.status === 'ok').map(f => f.file);
  if (ok.length) return { files: ok, label: `${ok.length} nahraných souborů` };
  return { texts: SAMPLE_DOCS, label: 'ukázková data' };
}

function startTraining({ paused = false, stepAfter = false } = {}) {
  const vocab = Math.max(500, Math.min(32000, Math.round(+$('vocabInput').value || 2000)));
  $('vocabInput').value = vocab;
  const mb = Math.max(0.1, Math.min(200, +$('sampleMBInput').value || 5));
  const src = trainSource();
  if (src.texts && !S.useSample) { S.useSample = true; renderFiles(); refreshSources(); }
  S.train = { idToToken: [], merges: [], state: 'reading', info: {}, history: [], vocabSize: vocab, srcLabel: src.label };
  S.trainedTok = null; S.trainedTokAt = -1;
  cptChart.setData([]); histChart.setData([]);
  $('mergeRows').innerHTML = '<tr class="empty"><td colspan="4">Zatím žádná spojení.</td></tr>';
  $('longestList').innerHTML = '<li class="muted">Zatím nic.</li>';
  $('trainMsg').textContent = `Čtu ${src.label} (nejvýš ${nf1.format(mb)} MB)…`;
  trainWorker.postMessage({ type: 'start', ...src, label: undefined, field: $('fieldInput').value.trim() || 'text', sampleMB: mb, vocabSize: vocab, paused, stepAfter });
  updateButtons();
}

$('startBtn').addEventListener('click', () => startTraining());
$('pauseBtn').addEventListener('click', () => trainWorker.postMessage({ type: 'pause' }));
$('resumeBtn').addEventListener('click', () => trainWorker.postMessage({ type: 'resume' }));
$('stepBtn').addEventListener('click', () => {
  if (!S.train || S.train.state === 'idle') startTraining({ paused: true, stepAfter: true });
  else trainWorker.postMessage({ type: 'step' });
});
$('resetBtn').addEventListener('click', () => trainWorker.postMessage({ type: 'reset' }));
$('stepSentence').addEventListener('input', () => renderStepStatic());

function updateButtons() {
  const st = S.train?.state || 'idle';
  const busy = st === 'reading';
  $('startBtn').disabled = busy || st === 'running';
  $('startBtn').textContent = st === 'idle' ? '▶ Start' : '▶ Začít znovu';
  $('pauseBtn').disabled = st !== 'running';
  $('resumeBtn').disabled = st !== 'paused';
  $('stepBtn').disabled = busy || st === 'done';
  $('resetBtn').disabled = st === 'idle';
  const has = S.train && S.train.merges.length > 0;
  $('dlJsonBtn').disabled = !has; $('dlCsvBtn').disabled = !has;
}

function trainedTokenizer() {
  const T = S.train;
  if (!T || !T.idToToken.length) return null;
  if (S.trainedTok && S.trainedTokAt === T.merges.length) return S.trainedTok;
  const data = trainedData();
  S.trainedTok = attachRank(new BpeTokenizer(data));
  S.trainedTokAt = T.merges.length;
  return S.trainedTok;
}
function trainedData() {
  const T = S.train;
  return {
    idToToken: T.idToToken.slice(),
    merges: T.merges.map(([a, b]) => [T.idToToken[a], T.idToToken[b]]),
    mergeCounts: T.merges.map(m => m[3]),
    specialTokens: [EOT],
  };
}

let pendingProgress = null, progRaf = 0;
trainWorker.onmessage = e => {
  const m = e.data;
  const T = S.train;
  if (m.type === 'phase') {
    $('progBar').style.width = `${Math.round(m.frac * 100)}%`;
    $('trainMsg').textContent = m.phase === 'reading'
      ? `Čtu a předtokenizuji data… ${Math.round(m.frac * 100)} % ${m.label ? `(${m.label})` : ''}`
      : `Připravuji počáteční četnosti dvojic… ${m.label || ''}`;
    return;
  }
  if (m.type === 'reset') {
    S.train = null; S.trainedTok = null;
    $('progBar').style.width = '0%';
    for (const id of ['stMerges', 'stVocab', 'stTime', 'stRate', 'stCpt', 'stSample']) $(id).textContent = '–';
    $('trainMsg').textContent = 'Trénink zrušen. Nastavte parametry a stiskněte Start.';
    cptChart.setData([]); histChart.setData([]);
    $('mergeRows').innerHTML = '<tr class="empty"><td colspan="4">Zatím žádná spojení.</td></tr>';
    $('longestList').innerHTML = '<li class="muted">Zatím nic.</li>';
    renderStepStatic();
    updateButtons(); refreshTokSelect();
    return;
  }
  if (m.type === 'error') { $('trainMsg').textContent = `Chyba: ${m.message}`; if (T) T.state = 'idle'; updateButtons(); return; }
  if (m.type !== 'progress' || !T) return;
  // průběžná data se slučují vždy (nesmí se ztratit), vykreslení max. 1× za snímek
  if (m.init) T.idToToken = [];
  T.idToToken.push(...m.newTokens);
  for (const nm of m.newMerges) T.merges.push(nm);
  T.history.push(...m.points);
  T.state = m.state; T.info = m.info;
  T.newRows = (T.newRows || 0) + m.newMerges.length;
  if (m.stepped) { renderProgress(m); animateStep(m.stepped); return; }
  pendingProgress = m;
  if (!progRaf) progRaf = requestAnimationFrame(() => { progRaf = 0; if (pendingProgress) renderProgress(pendingProgress); pendingProgress = null; });
};

function renderProgress(m) {
  const T = S.train;
  const target = Math.max(1, T.vocabSize - (T.info.baseVocab || 257));
  $('progBar').style.width = `${Math.min(100, (m.vocabLen - (T.info.baseVocab || 257)) / target * 100)}%`;
  $('stMerges').textContent = `${nf.format(m.merges)} / ${nf.format(target)}`;
  $('stVocab').textContent = nf.format(m.vocabLen);
  $('stTime').textContent = `${nf1.format(m.elapsedMs / 1000)} s`;
  $('stRate').textContent = m.elapsedMs > 50 ? `${nf.format(Math.round(m.merges / (m.elapsedMs / 1000)))} spoj./s` : '–';
  $('stCpt').textContent = nf2.format(m.cpt);
  const inf = T.info;
  $('stSample').textContent = `${fmtSize(inf.sampleBytes || 0)}`;
  const stateText = { running: 'Trénuji…', paused: 'Pozastaveno.', done: 'Hotovo ✓', reading: 'Čtu data…' }[m.state] || '';
  $('trainMsg').textContent = `${stateText} Zdroj: ${T.srcLabel} · ${nf.format(inf.docs || 0)} dokumentů · ${nf.format(inf.uniqueWords || 0)} unikátních slov · ${nf.format(m.totalTokens)} tokenů ve vzorku` +
    (inf.truncated ? ' · data oříznuta na nastavený vzorek' : '') +
    (m.state === 'done' && m.vocabLen < T.vocabSize ? ' · slovník menší než cíl: dvojice došly (málo dat)' : '');

  // tabulka posledních spojení
  const last = T.merges.slice(-15).reverse();
  const fresh = Math.min(T.newRows || 0, 15); T.newRows = 0;
  const tok = T.idToToken;
  const vis = s => esc(s);
  $('mergeRows').innerHTML = last.length ? last.map(([a, b, id, count], i) => {
    const r = T.merges.length - i;
    return `<tr${i < fresh && m.state !== 'running' ? ' class="new"' : ''}><td class="num">${nf.format(r)}</td>
      <td><span class="vt">${vis(dispTok(tok[a]))}</span> + <span class="vt">${vis(dispTok(tok[b]))}</span></td>
      <td>→ <span class="vt">${vis(dispTok(tok[id]))}</span></td><td class="num">${nf.format(count)}</td></tr>`;
  }).join('') : '<tr class="empty"><td colspan="4">Zatím žádná spojení.</td></tr>';

  cptChart.setData(T.history);
  histChart.setData(m.hist);
  $('longestList').innerHTML = m.longest.length
    ? m.longest.map(({ id, token }) => `<li><span class="vt">${esc(dispTok(token))}</span><span>${token.length} B · ID ${id}</span></li>`).join('')
    : '<li class="muted">Zatím nic.</li>';
  updateButtons();
  if (m.state === 'done' || m.init) { refreshTokSelect(); renderStepStatic(); }
  if (m.state === 'paused' && !m.stepped) refreshTokSelect();
}

/** bytově-úrovňový řetězec → čitelný text (mezery jako ·, neúplné bajty jako ‹xx›) */
function dispTok(t) {
  if (t === EOT) return t;
  const bytes = tokenBytes(t);
  let s;
  try { s = strictUtf8.decode(new Uint8Array(bytes)); }
  catch { return bytes.map(b => (b >= 0x20 && b < 0x7f ? String.fromCharCode(b) : `‹${b.toString(16).padStart(2, '0')}›`)).join('').replace(/ /g, '·'); }
  return s.replace(/ /g, '·').replace(/\n/g, '↵').replace(/\t/g, '→');
}

// krokování s animací
let stepTimer = 0;
function renderStepStatic() {
  const tok = trainedTokenizer();
  const s = $('stepSentence').value;
  if (!tok) {
    // bez tréninku: jen bajty
    const base = attachRank(new BpeTokenizer({ idToToken: [EOT, ...ALPHABET], merges: [] }));
    renderTokens($('stepTokens'), base, base.encode(s).ids, { focusable: true });
    return;
  }
  renderTokens($('stepTokens'), tok, tok.encode(s).ids, { focusable: true });
}

function animateStep(st) {
  const tok = trainedTokenizer();
  const T = S.train;
  const s = $('stepSentence').value;
  const before = tok.encode(s, { maxMerges: st.rank }).ids;
  const after = tok.encode(s, { maxMerges: st.rank + 1 }).ids;
  const pairs = new Set();
  for (let i = 0; i + 1 < before.length; i++) {
    if (before[i] === st.a && before[i + 1] === st.b) { pairs.add(i); i++; }
  }
  const A = dispTok(T.idToToken[st.a]), B = dispTok(T.idToToken[st.b]), N = dispTok(T.idToToken[st.id]);
  const occ = pairs.size;
  $('stepCaption').innerHTML = `Spojení <b>č. ${nf.format(st.rank + 1)}</b>: <code>${esc(A)}</code> + <code>${esc(B)}</code> → <code>${esc(N)}</code>` +
    ` <span class="muted">· v datech ${nf.format(st.count)}× · ve větě ${occ ? `${occ}×` : 'není'}</span>`;
  clearTimeout(stepTimer);
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  renderTokens($('stepTokens'), tok, before, {
    focusable: true,
    cls: i => (pairs.has(i) ? ' pair left' : pairs.has(i - 1) ? ' pair right' : ''),
  });
  stepTimer = setTimeout(() => {
    renderTokens($('stepTokens'), tok, after, { focusable: true, cls: i => (after[i] === st.id && occ ? ' fresh' : '') });
  }, occ ? (reduce ? 300 : 900) : 0);
}
renderStepStatic();

// stažení / načtení
function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = Object.assign(document.createElement('a'), { href: url, download: name });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$('dlJsonBtn').addEventListener('click', () => download('tokenizer.json', toHfJson(trainedData()), 'application/json'));
$('dlCsvBtn').addEventListener('click', () => {
  const tok = trainedTokenizer();
  download('tokenizer-slovnik.csv', toCsv(trainedData(), id => tokenText(tok, id).text), 'text/csv;charset=utf-8');
});
$('loadTokInput').addEventListener('change', async e => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  try {
    const data = fromHfJson(await file.text());
    const tok = attachRank(new BpeTokenizer(data));
    S.loaded = { name: file.name, data, tok };
    $('loadMsg').innerHTML = `✓ Načten <b>${esc(file.name)}</b>: ${nf.format(tok.vocabSize)} tokenů, ${nf.format(tok.numMerges)} spojení.` +
      (data.warnings.length ? ` <span class="status-bad">${esc(data.warnings.join(' '))}</span>` : '') +
      ' Použije se v prohlížeči a pipeline.';
    refreshTokSelect('loaded');
  } catch (err) {
    $('loadMsg').innerHTML = `<span class="status-bad">Soubor nejde načíst: ${esc(err.message)}</span>`;
  }
});

// ---------------------------------------------------------------- 3) prohlížeč
const DEFAULT_VIEW = `${DEMO_SENTENCE}\nDnes jsem konečně dočetl knihu, kterou mi půjčil děda. Žluťoučký kůň úpěl ďábelské ódy.`;
$('viewText').value = DEFAULT_VIEW;

function refreshTokSelect(prefer) {
  const sel = $('viewTok');
  const cur = prefer || sel.value;
  const opts = [];
  const tt = S.train && S.train.merges.length ? trainedTokenizer() : null;
  if (tt) opts.push(['trained', `natrénovaný zde (${nf.format(tt.numMerges)} spojení)`]);
  if (S.loaded) opts.push(['loaded', `načtený: ${S.loaded.name} (${nf.format(S.loaded.tok.vocabSize)})`]);
  if (!opts.length) opts.push(['', '— žádný (jen bajty) —']);
  sel.innerHTML = opts.map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join('');
  sel.value = opts.some(o => o[0] === cur) ? cur : opts[0][0];
  const active = currentTok();
  const chip = $('tokChip');
  chip.textContent = active ? `Tokenizér: ${sel.value === 'loaded' ? S.loaded.name : 'natrénovaný'} · ${nf.format(active.vocabSize)} tokenů` : 'Tokenizér: žádný';
  chip.classList.toggle('on', !!active);
  configureSlider();
  if (!$('panel-view').hidden) renderView();
}

function currentTok() {
  const v = $('viewTok').value;
  if (v === 'loaded' && S.loaded) return S.loaded.tok;
  if (v === 'trained') return trainedTokenizer();
  return null;
}
const BYTE_TOK = attachRank(new BpeTokenizer({ idToToken: [EOT, ...ALPHABET], merges: [] }));

function configureSlider() {
  const tok = currentTok() || BYTE_TOK;
  const sl = $('mergeSlider');
  const wasMax = +sl.value >= +sl.max;
  sl.max = tok.numMerges;
  if (wasMax || +sl.value > tok.numMerges) sl.value = tok.numMerges;
  updateMergeLabel();
}
function updateMergeLabel() {
  const sl = $('mergeSlider');
  $('mergeVal').textContent = +sl.value >= +sl.max ? `vše (${nf.format(+sl.max)})` : nf.format(+sl.value);
  for (const b of document.querySelectorAll('[data-merges]')) {
    const v = b.dataset.merges === 'all' ? +sl.max : +b.dataset.merges;
    b.classList.toggle('active', v === +sl.value);
    b.disabled = b.dataset.merges !== 'all' && v > +sl.max;
  }
}
$('mergeSlider').addEventListener('input', () => { updateMergeLabel(); renderViewSoon(); });
document.querySelectorAll('[data-merges]').forEach(b => b.addEventListener('click', () => {
  const sl = $('mergeSlider');
  sl.value = b.dataset.merges === 'all' ? sl.max : Math.min(+b.dataset.merges, +sl.max);
  updateMergeLabel(); renderView();
}));
$('viewTok').addEventListener('change', () => refreshTokSelect());
$('viewText').addEventListener('input', () => { $('decoded').hidden = true; $('decodeResult').textContent = ''; renderViewSoon(); });
$('showSpaces').addEventListener('change', () => renderView());
$('showWords').addEventListener('change', () => renderView());
$('viewSource').addEventListener('change', e => {
  const v = e.target.value;
  if (v === 'sample') $('viewText').value = SAMPLE_TEXT;
  else if (v) { const f = S.files.find(x => String(x.id) === v); if (f?.sample) $('viewText').value = f.sample.trim(); }
  renderView();
});

function refreshSources() {
  const opts = ['<option value="">— vlastní text —</option>'];
  if (S.useSample) opts.push('<option value="sample">ukázková data</option>');
  for (const f of S.files) if (f.status === 'ok') opts.push(`<option value="${f.id}">${esc(f.file.name)} (začátek)</option>`);
  $('viewSource').innerHTML = opts.join('');
}

let viewTimer = 0;
function renderViewSoon() { clearTimeout(viewTimer); viewTimer = setTimeout(renderView, 120); }

function renderView() {
  const tok = currentTok() || BYTE_TOK;
  const text = $('viewText').value;
  const sl = $('mergeSlider');
  const k = +sl.value >= +sl.max ? Infinity : +sl.value;
  const t0 = performance.now();
  const { ids, words } = tok.encode(text, { maxMerges: k, withWords: true });
  const ms = performance.now() - t0;
  const chars = charCount(text);
  const bytes = new TextEncoder().encode(text).length;
  $('vsChars').textContent = nf.format(chars);
  $('vsBytes').textContent = nf.format(bytes);
  $('vsTokens').textContent = nf.format(ids.length);
  $('vsCpt').textContent = ids.length ? nf2.format(chars / ids.length) : '–';
  const box = $('viewTokens');
  box.classList.toggle('words', $('showWords').checked);
  renderTokens(box, tok, ids, { words, showSpaces: $('showSpaces').checked, limit: 20000, focusable: ids.length <= 400 });
  $('viewIds').textContent = ids.slice(0, 20000).join(', ') + (ids.length > 20000 ? ' …' : '');
  $('viewNote').textContent = (currentTok() ? '' : 'Bez tokenizéru: každý bajt je jeden token. Natrénujte nebo načtěte tokenizér. ') +
    `Najeďte na token pro detail (ID, bajty). Tokenizace trvala ${nf1.format(ms)} ms.`;
  renderCompare(tok, text, chars);
}

function renderCompare(tok, text, chars) {
  const max = tok.numMerges;
  const ks = [...new Set([0, 100, 500, 2000, max].filter(v => v <= max))];
  const head = text.slice(0, 160);
  const rows = ks.map(k => {
    const n = tok.encode(text, { maxMerges: k }).ids.length;
    const hid = tok.encode(head, { maxMerges: k }).ids;
    const tmp = document.createElement('div');
    renderTokens(tmp, tok, hid, { limit: 40, showSpaces: true });
    return `<tr><td class="num">${k === max ? `vše (${nf.format(k)})` : nf.format(k)}</td><td class="num">${nf.format(n)}</td>
      <td class="num">${n ? nf2.format(chars / n) : '–'}</td><td class="tokens-inline">${tmp.innerHTML}</td></tr>`;
  });
  $('compareRows').innerHTML = rows.join('');
  $('compareRows')._tok = tok;
}

$('decodeBtn').addEventListener('click', () => {
  const tok = currentTok() || BYTE_TOK;
  const text = $('viewText').value;
  const sl = $('mergeSlider');
  const { ids } = tok.encode(text, { maxMerges: +sl.value >= +sl.max ? Infinity : +sl.value });
  const back = tok.decode(ids);
  const same = back === text;
  $('decoded').hidden = false;
  $('decoded').textContent = back;
  $('decodeResult').innerHTML = same
    ? `<span class="status-ok">✓ Shoda: ${nf.format(ids.length)} tokenů → stejný text (${nf.format(charCount(back))} znaků), beze ztráty.</span>`
    : '<span class="status-bad">✘ Text se liší!</span>';
});

// ---------------------------------------------------------------- 4) pipeline
$('pipeBtn').addEventListener('click', () => {
  const tok = currentTok();
  if (!tok) { $('pipeMsg').innerHTML = '<span class="status-bad">Nejdřív natrénujte nebo načtěte tokenizér (záložka 2).</span>'; return; }
  const ok = S.files.filter(f => f.status === 'ok').map(f => f.file);
  const src = ok.length ? { files: ok } : { texts: SAMPLE_DOCS };
  const valFraction = Math.max(0, Math.min(50, +$('valInput').value || 0)) / 100;
  $('pipeBtn').disabled = true;
  $('pipeBar').style.width = '0%';
  $('pipeMsg').textContent = `Tokenizuji ${ok.length ? `${ok.length} souborů` : 'ukázková data'}…`;
  dataWorker.postMessage({
    type: 'pipeline', ...src, field: $('fieldInput').value.trim() || 'text', valFraction,
    maxMB: +$('pipeMBInput').value || 50,
    tok: { idToToken: tok.idToToken, merges: tok.merges, specialTokens: tok.specialTokens },
  });
  S.pipeTok = tok; S.pipeVal = valFraction;
});

function pipeProgress(m) {
  $('pipeBar').style.width = `${Math.round(m.frac * 100)}%`;
  const tokens = m.r.train.tokens + m.r.val.tokens;
  $('pipeMsg').textContent = `Tokenizuji… ${Math.round(m.frac * 100)} % · ${nf.format(m.r.docs)} dokumentů · ${nf.format(tokens)} tokenů · ${nf.format(Math.round(tokens / (m.ms / 1000)))} tokenů/s`;
}

function pipeDone(m) {
  const r = m.r, tok = S.pipeTok;
  $('pipeBtn').disabled = false;
  $('pipeBar').style.width = '100%';
  const tot = r.train.tokens + r.val.tokens;
  $('pipeMsg').textContent = `Hotovo za ${nf1.format(m.ms / 1000)} s · ${fmtSize(r.bytes)} vstupu · ${nf.format(Math.round(tot / Math.max(0.001, m.ms / 1000)))} tokenů/s` +
    (r.truncated ? ' · zpracováno jen do nastaveného limitu MB' : '');
  $('fDocs').textContent = `${nf.format(r.docs)} dokumentů · ${nf.format(r.chars)} znaků`;
  $('fTok').textContent = `${nf.format(tot - (r.hasEot ? r.docs : 0))} tokenů · ${nf2.format(r.chars / Math.max(1, tot - r.docs))} zn./token`;
  $('fEot').textContent = r.hasEot ? `+ ${nf.format(r.docs)}× <|endoftext|>` : 'tokenizér nemá <|endoftext|>!';
  $('fSplit').textContent = `${nf.format(r.train.docs)} train / ${nf.format(r.val.docs)} val`;
  $('fBin').textContent = `${fmtSize(tot * 2)} celkem`;
  const row = (name, x) => `<tr><th scope="row">${name}</th><td class="num">${nf.format(x.docs)}</td><td class="num">${nf.format(x.tokens)}</td><td class="num">${fmtSize(x.tokens * 2)}</td></tr>`;
  $('pipeRows').innerHTML = row('train.bin', r.train) + row('val.bin', r.val) +
    row('celkem', { docs: r.docs, tokens: tot });
  const checks = [];
  checks.push(tok.vocabSize < 65536 ? `✓ slovník ${nf.format(tok.vocabSize)} < 65 536 → stačí uint16 (2 bajty na token)` : '✘ slovník je větší než 65 536 – uint16 nestačí');
  checks.push(r.hasEot ? '✓ <|endoftext|> odděluje dokumenty' : '✘ chybí <|endoftext|>');
  if (r.val.docs === 0) checks.push(`⚠ validační část je prázdná – při ${nf1.format(S.pipeVal * 100)} % je potřeba víc dokumentů (zkuste vyšší podíl)`);
  checks.push('Rozdělení je náhodné po dokumentech (seed 2005), jako v prepare.py; konkrétní výběr se od Pythonu liší.');
  $('pipeChecks').innerHTML = checks.map(esc).join('<br>');
  // náhled proudu
  const ids = [];
  const eot = tok.tokenToId.get(EOT);
  for (const p of r.preview) {
    ids.push(...p.ids);
    if (p.total - (eot !== undefined ? 1 : 0) > p.ids.length) ids.push(-1);
    if (eot !== undefined) ids.push(eot);
  }
  const box = $('pipePreview');
  const real = ids.filter(x => x >= 0);
  renderTokens(box, tok, real, { showSpaces: true });
  // vložit „…“ tam, kde je dokument zkrácen
  let k = 0;
  const spans = [...box.children];
  ids.forEach(x => { if (x < 0) spans[k - 1]?.insertAdjacentHTML('afterend', '<span class="muted"> … </span>'); else k++; });
}

// ---------------------------------------------------------------- start
applyTheme(store.get('theme') || 'system');
renderFiles();
refreshSources();
refreshTokSelect();
updateButtons();
const savedTab = $(store.get('tab') || '');
if (savedTab && tabs.includes(savedTab)) selectTab(savedTab);
