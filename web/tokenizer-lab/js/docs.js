// Čtení nahraných souborů po částech (bez načtení celého souboru do paměti najednou).
// Vrací dokumenty po kouscích: {text, end} – `end` = konec dokumentu.

export const CHUNK = 4 << 20; // 4 MB

export function kindOf(name) {
  const ext = name.toLowerCase().split('.').pop();
  if (ext === 'jsonl' || ext === 'ndjson') return 'jsonl';
  if (ext === 'csv') return 'csv';
  return 'text'; // .txt, .md a cokoliv jiného jako čistý text
}

/** Čte soubor po částech jako text (UTF-8, správně přes hranice bloků). */
async function* readChunks(file, maxBytes = Infinity) {
  const dec = new TextDecoder('utf-8');
  const end = Math.min(file.size, maxBytes);
  for (let pos = 0; pos < end; pos += CHUNK) {
    const buf = await file.slice(pos, Math.min(pos + CHUNK, end)).arrayBuffer();
    const last = pos + CHUNK >= end;
    yield { text: dec.decode(buf, { stream: !last }), bytes: Math.min(pos + CHUNK, end) };
  }
}

/** Najde místo řezu tak, aby se nerozdělilo slovo: začátek posledního úseku mezer. */
function cutPoint(s) {
  let i = s.length - 1;
  while (i >= 0 && !/\s/.test(s[i])) i--;
  if (i < 0) return -1;
  while (i > 0 && /\s/.test(s[i - 1])) i--;
  return i;
}

/**
 * Dokumenty ze souboru.
 * @param {File|Blob} file
 * @param {{field?:string, maxBytes?:number, onBytes?:(n:number)=>void, stats?:object}} opt
 */
export async function* iterDocs(file, { field = 'text', maxBytes = Infinity, onBytes, stats = {} } = {}) {
  const kind = kindOf(file.name || 'x.txt');
  stats.badLines = 0;
  stats.missingField = 0;

  if (kind === 'text') {
    let carry = '';
    for await (const { text, bytes } of readChunks(file, maxBytes)) {
      onBytes?.(bytes);
      const s = carry + text;
      const cut = cutPoint(s);
      if (cut <= 0 || s.length - cut > 1 << 20) { carry = ''; if (s) yield { text: s, end: false }; }
      else { carry = s.slice(cut); yield { text: s.slice(0, cut), end: false }; }
    }
    yield { text: carry, end: true };
    return;
  }

  if (kind === 'jsonl') {
    let carry = '';
    const handle = line => {
      line = line.trim();
      if (!line) return null;
      try {
        const obj = JSON.parse(line);
        const v = obj && typeof obj === 'object' ? obj[field] : undefined;
        if (typeof v === 'string') return v;
        stats.missingField++;
      } catch { stats.badLines++; }
      return null;
    };
    for await (const { text, bytes } of readChunks(file, maxBytes)) {
      onBytes?.(bytes);
      const lines = (carry + text).split('\n');
      carry = lines.pop();
      for (const l of lines) { const d = handle(l); if (d !== null) yield { text: d, end: true }; }
    }
    // poslední řádek: při ořezu maxBytes může být neúplný – pak ho přeskočit
    if (carry.trim() && file.size <= maxBytes) { const d = handle(carry); if (d !== null) yield { text: d, end: true }; }
    return;
  }

  // CSV: stavový automat (uvozovky, "" uvnitř, nové řádky v poli)
  let header = null, col = -1;
  let row = [], fieldBuf = '', inQ = false, quoteSeen = false;
  const pending = [];
  const finishRow = () => {
    row.push(fieldBuf); fieldBuf = '';
    if (!header) {
      header = row.map(h => h.trim().replace(/^﻿/, ''));
      col = header.indexOf(field);
    } else if (row.length > 1 || row[0] !== '') {
      if (col >= 0 && row[col] === undefined) stats.missingField++;
      pending.push(col >= 0 ? (row[col] ?? '') : row.join(', '));
    }
    row = [];
  };
  for await (const { text, bytes } of readChunks(file, maxBytes)) {
    onBytes?.(bytes);
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (inQ) {
        if (ch === '"') { inQ = false; quoteSeen = true; }
        else fieldBuf += ch;
        continue;
      }
      if (ch === '"') { if (quoteSeen) fieldBuf += '"'; inQ = true; quoteSeen = false; continue; }
      quoteSeen = false;
      if (ch === ',') { row.push(fieldBuf); fieldBuf = ''; }
      else if (ch === '\n') finishRow();
      else if (ch !== '\r') fieldBuf += ch;
    }
    while (pending.length) yield { text: pending.shift(), end: true };
  }
  if (fieldBuf || row.length) finishRow();
  while (pending.length) yield { text: pending.shift(), end: true };
  stats.csvColumn = col >= 0 ? field : null;
}

/** Statistika souboru: znaky, odhad slov, dokumenty, krátký náhled. */
export async function scanFile(file, { field = 'text', onProgress } = {}) {
  const stats = {};
  let chars = 0, words = 0, docs = 0, sample = '';
  let lastPost = 0;
  for await (const { text, end } of iterDocs(file, {
    field, stats,
    onBytes: b => { const t = Date.now(); if (t - lastPost > 100) { lastPost = t; onProgress?.(b / file.size); } },
  })) {
    for (let i = 0; i < text.length; i++) {
      const c = text.charCodeAt(i);
      if (c < 0xdc00 || c > 0xdfff) chars++;
    }
    const m = text.match(/\S+/g);
    if (m) words += m.length;
    if (sample.length < 4000) sample += text.slice(0, 4000 - sample.length) + (end ? '\n\n' : '');
    if (end) docs++;
  }
  return { chars, words, docs, sample, badLines: stats.badLines || 0, missingField: stats.missingField || 0, csvColumn: stats.csvColumn ?? null };
}
