// BPE trenér a tokenizér kompatibilní s Hugging Face `tokenizers` (models.BPE + ByteLevel).
//
// Trénink napodobuje BpeTrainer z Rustu:
//  * slovník = speciální tokeny, pak 256 bajtových znaků seřazených podle kódu,
//  * v každém kroku se sloučí dvojice s nejvyšší četností; při shodě vyhrává dvojice
//    s menšími ID (a, b) – stejné pravidlo jako `Ord for Merge` v `tokenizers`,
//  * líná prioritní fronta + mapa „kde se dvojice vyskytuje“.

import { ALPHABET, EOT, pretokenize, toByteLevel, splitSpecial, tokenBytes, bytesToString, charCount } from './bytelevel.js';

const K = 65536; // klíč dvojice = a * K + b (slovník < 65 536)
const pairKey = (a, b) => a * K + b;

/** Max-halda nad (count desc, key asc). */
class MergeHeap {
  constructor() { this.c = []; this.k = []; }
  get size() { return this.c.length; }
  _gt(i, j) { return this.c[i] > this.c[j] || (this.c[i] === this.c[j] && this.k[i] < this.k[j]); }
  _sw(i, j) {
    const c = this.c[i]; this.c[i] = this.c[j]; this.c[j] = c;
    const k = this.k[i]; this.k[i] = this.k[j]; this.k[j] = k;
  }
  push(count, key) {
    this.c.push(count); this.k.push(key);
    let i = this.c.length - 1;
    while (i > 0) { const p = (i - 1) >> 1; if (!this._gt(i, p)) break; this._sw(i, p); i = p; }
  }
  pop() {
    const n = this.c.length;
    if (!n) return null;
    const top = { count: this.c[0], key: this.k[0] };
    const lc = this.c.pop(), lk = this.k.pop();
    if (n > 1) {
      this.c[0] = lc; this.k[0] = lk;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1, r = l + 1; let m = i;
        if (l < n - 1 && this._gt(l, m)) m = l;
        if (r < n - 1 && this._gt(r, m)) m = r;
        if (m === i) break;
        this._sw(i, m); i = m;
      }
    }
    return top;
  }
}

/** Spočítá „slova“ (předtokeny) v textu do mapy. */
export function countWords(text, map = new Map()) {
  for (const w of pretokenize(text)) {
    map.set(w, (map.get(w) || 0) + 1);
  }
  return map;
}

export class BpeTrainer {
  /**
   * @param {Map<string, number>} rawWordCounts  předtoken (původní text) → četnost
   * @param {{vocabSize:number, specialTokens?:string[]}} opts
   */
  constructor(rawWordCounts, { vocabSize = 2000, specialTokens = [EOT] } = {}) {
    this.vocabSize = vocabSize;
    this.specialTokens = specialTokens;
    this.idToToken = [...specialTokens, ...ALPHABET];
    this.tokenToId = new Map(this.idToToken.map((t, i) => [t, i]));
    this.merges = []; // [{a, b, id, count}]

    const byteWords = new Map();
    this.totalChars = 0;
    for (const [raw, cnt] of rawWordCounts) {
      const bl = toByteLevel(raw);
      byteWords.set(bl, (byteWords.get(bl) || 0) + cnt);
      this.totalChars += charCount(raw) * cnt;
    }
    this.words = [];
    this.counts = [];
    this.totalTokens = 0;
    for (const [bl, cnt] of byteWords) {
      const ids = [];
      for (const ch of bl) ids.push(this.tokenToId.get(ch));
      this.words.push(ids);
      this.counts.push(cnt);
      this.totalTokens += ids.length * cnt;
    }
    this.totalBytes = this.totalTokens;
    this.uniqueWords = this.words.length;

    // Počáteční četnosti dvojic.
    this.pairCounts = new Map();
    this.where = new Map();
    for (let wi = 0; wi < this.words.length; wi++) {
      const w = this.words[wi], cnt = this.counts[wi];
      for (let i = 0; i + 1 < w.length; i++) {
        const k = pairKey(w[i], w[i + 1]);
        this.pairCounts.set(k, (this.pairCounts.get(k) || 0) + cnt);
        let s = this.where.get(k);
        if (!s) { s = new Set(); this.where.set(k, s); }
        s.add(wi);
      }
    }
    this.heap = new MergeHeap();
    for (const [k, c] of this.pairCounts) if (c > 0) this.heap.push(c, k);
    this.done = false;
  }

  get vocabLen() { return this.idToToken.length; }
  get charsPerToken() { return this.totalTokens ? this.totalChars / this.totalTokens : 0; }

  /** Provede jedno spojení. Vrátí popis spojení, nebo null, když je hotovo. */
  step() {
    if (this.done) return null;
    if (this.idToToken.length >= this.vocabSize) { this.done = true; return null; }
    let top;
    for (;;) {
      top = this.heap.pop();
      if (!top) { this.done = true; return null; }
      const cur = this.pairCounts.get(top.key) || 0;
      if (cur !== top.count) { if (cur > 0) this.heap.push(cur, top.key); continue; }
      break;
    }
    if (top.count < 1) { this.done = true; return null; }

    const a = Math.floor(top.key / K), b = top.key % K;
    const tokA = this.idToToken[a], tokB = this.idToToken[b];
    const newTok = tokA + tokB;
    let newId = this.tokenToId.get(newTok);
    if (newId === undefined) {
      newId = this.idToToken.length;
      this.idToToken.push(newTok);
      this.tokenToId.set(newTok, newId);
    }
    this.merges.push({ a, b, id: newId, count: top.count });

    // Sloučení ve všech slovech, kde se dvojice (možná) vyskytuje – stejně jako Word::merge.
    const touched = this.where.get(top.key);
    this.where.delete(top.key);
    const delta = new Map();
    const newWhere = new Map();
    const add = (k, d, wi) => {
      delta.set(k, (delta.get(k) || 0) + d);
      if (d > 0) {
        let s = newWhere.get(k);
        if (!s) { s = new Set(); newWhere.set(k, s); }
        s.add(wi);
      }
    };
    let mergedOccurrences = 0;
    if (touched) {
      for (const wi of touched) {
        const w = this.words[wi], cnt = this.counts[wi];
        let i = 0;
        while (i < w.length) {
          if (w[i] === a && i + 1 < w.length && w[i + 1] === b) {
            if (i > 0) { add(pairKey(w[i - 1], a), -cnt, wi); add(pairKey(w[i - 1], newId), cnt, wi); }
            w.splice(i, 2, newId);
            if (i < w.length - 1) { add(pairKey(b, w[i + 1]), -cnt, wi); add(pairKey(newId, w[i + 1]), cnt, wi); }
            mergedOccurrences += cnt;
          }
          i++;
        }
      }
    }
    this.totalTokens -= mergedOccurrences;
    for (const [k, d] of delta) this.pairCounts.set(k, (this.pairCounts.get(k) || 0) + d);
    for (const [k, s] of newWhere) {
      let ws = this.where.get(k);
      if (!ws) { ws = new Set(); this.where.set(k, ws); }
      for (const wi of s) ws.add(wi);
      const c = this.pairCounts.get(k) || 0;
      if (c > 0) this.heap.push(c, k);
    }
    return { rank: this.merges.length - 1, a, b, tokA, tokB, id: newId, token: newTok, count: top.count };
  }

  /** Nejdelší naučené tokeny (podle počtu bajtů). */
  longestTokens(n = 12) {
    const start = this.specialTokens.length + 256;
    const arr = [];
    for (let id = start; id < this.idToToken.length; id++) arr.push(id);
    arr.sort((x, y) => this.idToToken[y].length - this.idToToken[x].length || x - y);
    return arr.slice(0, n).map(id => ({ id, token: this.idToToken[id] }));
  }

  /** Histogram délek (v bajtech) naučených tokenů (bez 256 základních bajtů). */
  lengthHistogram(maxLen = 12) {
    const h = new Array(maxLen).fill(0);
    const start = this.specialTokens.length + 256;
    for (let id = start; id < this.idToToken.length; id++) {
      const L = Math.min(this.idToToken[id].length, maxLen);
      h[L - 1]++;
    }
    return h;
  }

  /** Data pro export / tokenizér. */
  snapshot() {
    return {
      idToToken: this.idToToken.slice(),
      merges: this.merges.map(m => [this.idToToken[m.a], this.idToToken[m.b]]),
      mergeCounts: this.merges.map(m => m.count),
      specialTokens: this.specialTokens.slice(),
    };
  }
}

/** Tokenizér (enkódování / dekódování) nad slovníkem a seznamem spojení. */
export class BpeTokenizer {
  /**
   * @param {{idToToken:string[], merges:[string,string][], specialTokens?:string[]}} data
   */
  constructor({ idToToken, merges, specialTokens = [EOT] }) {
    this.idToToken = idToToken;
    this.tokenToId = new Map(idToToken.map((t, i) => [t, i]));
    this.specialTokens = specialTokens.filter(s => this.tokenToId.has(s));
    this.merges = merges;
    this.mergeInfo = new Map(); // klíč dvojice → [rank, newId]
    merges.forEach(([x, y], rank) => {
      const a = this.tokenToId.get(x), b = this.tokenToId.get(y), n = this.tokenToId.get(x + y);
      if (a === undefined || b === undefined || n === undefined) return;
      const k = pairKey(a, b);
      if (!this.mergeInfo.has(k)) this.mergeInfo.set(k, [rank, n]);
    });
    this.caches = new Map();
  }

  get vocabSize() { return this.idToToken.length; }
  get numMerges() { return this.merges.length; }

  /** Rozdělí jedno bytově-úrovňové slovo pomocí spojení s rankem < maxMerges. */
  encodeWord(bl, maxMerges = Infinity) {
    let cache = this.caches.get(maxMerges);
    if (!cache) {
      cache = new Map(); this.caches.set(maxMerges, cache);
      if (this.caches.size > 8) this.caches.delete(this.caches.keys().next().value);
    }
    const hit = cache.get(bl);
    if (hit) return hit;
    const sym = [];
    for (const ch of bl) sym.push(this.tokenToId.get(ch));
    while (sym.length > 1) {
      let best = Infinity, bestI = -1, bestNew = -1;
      for (let i = 0; i + 1 < sym.length; i++) {
        const info = this.mergeInfo.get(pairKey(sym[i], sym[i + 1]));
        if (info && info[0] < best && info[0] < maxMerges) { best = info[0]; bestI = i; bestNew = info[1]; }
      }
      if (bestI < 0) break;
      const a = sym[bestI], b = sym[bestI + 1];
      for (let i = 0; i + 1 < sym.length; i++) {
        if (sym[i] === a && sym[i + 1] === b) sym.splice(i, 2, bestNew);
      }
    }
    if (cache.size > 200000) cache.clear();
    cache.set(bl, sym);
    return sym;
  }

  /**
   * @returns {{ids:number[], words:number[]|null}}
   *   words: pro každý token pořadí předtokenu, ze kterého vznikl (pro hranice slov)
   */
  encode(text, { maxMerges = Infinity, withWords = false } = {}) {
    const ids = [];
    const words = withWords ? [] : null;
    let wordIdx = 0;
    for (const part of splitSpecial(text, this.specialTokens)) {
      if (part.special) {
        ids.push(this.tokenToId.get(part.special));
        if (words) words.push(wordIdx);
        wordIdx++;
        continue;
      }
      for (const w of pretokenize(part.text)) {
        const sym = this.encodeWord(toByteLevel(w), maxMerges);
        for (const id of sym) {
          ids.push(id);
          if (words) words.push(wordIdx);
        }
        wordIdx++;
      }
    }
    return { ids, words };
  }

  /** Bajty tokenu (speciální tokeny jako UTF-8 jejich textu). */
  bytesOf(id) {
    const t = this.idToToken[id];
    if (this.specialTokens.includes(t)) return Array.from(new TextEncoder().encode(t));
    return tokenBytes(t);
  }

  decode(ids) {
    const out = [];
    let buf = [];
    for (const id of ids) {
      const t = this.idToToken[id];
      if (t === undefined) continue;
      if (this.specialTokens.includes(t)) { out.push(bytesToString(buf), t); buf = []; continue; }
      for (const b of tokenBytes(t)) buf.push(b);
    }
    out.push(bytesToString(buf));
    return out.join('');
  }

  /** Text tokenu pro zobrazení (dekódované bajty; neúplné UTF-8 → �). */
  display(id) {
    const t = this.idToToken[id];
    if (this.specialTokens.includes(t)) return t;
    return bytesToString(tokenBytes(t));
  }
}

/** Export do formátu Hugging Face `tokenizer.json`. */
export function toHfJson({ idToToken, merges, specialTokens = [EOT] }) {
  const vocab = {};
  idToToken.forEach((t, i) => { vocab[t] = i; }); // `tokenizers` ukládá i speciální tokeny do vocab
  const obj = {
    version: '1.0',
    truncation: null,
    padding: null,
    added_tokens: specialTokens.map(s => ({
      id: idToToken.indexOf(s), content: s, single_word: false, lstrip: false,
      rstrip: false, normalized: false, special: true,
    })),
    normalizer: null,
    pre_tokenizer: { type: 'ByteLevel', add_prefix_space: false, trim_offsets: true, use_regex: true },
    post_processor: null,
    decoder: { type: 'ByteLevel', add_prefix_space: true, trim_offsets: true, use_regex: true },
    model: {
      type: 'BPE', dropout: null, unk_token: null, continuing_subword_prefix: null,
      end_of_word_suffix: null, fuse_unk: false, byte_fallback: false, ignore_merges: false,
      vocab, merges: merges.map(([a, b]) => [a, b]),
    },
  };
  return JSON.stringify(obj, null, 2);
}

/** Načtení `tokenizer.json` (merges jako ["a","b"] i jako "a b"). */
export function fromHfJson(json) {
  const obj = typeof json === 'string' ? JSON.parse(json) : json;
  const m = obj.model;
  if (!m || m.type !== 'BPE') throw new Error('Soubor neobsahuje model typu BPE.');
  const warnings = [];
  if (!obj.pre_tokenizer || obj.pre_tokenizer.type !== 'ByteLevel') {
    warnings.push('Pre-tokenizer není ByteLevel – rozdělení se může lišit od Pythonu.');
  }
  const idToToken = [];
  for (const [t, id] of Object.entries(m.vocab)) idToToken[id] = t;
  const added = obj.added_tokens || [];
  for (const at of added) idToToken[at.id] = at.content;
  for (let i = 0; i < idToToken.length; i++) if (idToToken[i] === undefined) idToToken[i] = `<chybí ${i}>`;
  const merges = (m.merges || []).map(x => {
    if (Array.isArray(x)) return x;
    const s = x.indexOf(' ', 1);
    return [x.slice(0, s), x.slice(s + 1)];
  });
  const specialTokens = added.filter(a => a.special).map(a => a.content);
  return { idToToken, merges, specialTokens, warnings };
}

/** CSV se slovníkem a spojeními (čitelné, s BOM kvůli Excelu). */
export function toCsv({ idToToken, merges, mergeCounts, specialTokens = [EOT] }, display) {
  const q = s => '"' + String(s).replace(/"/g, '""') + '"';
  const lines = ['id,token_bytelevel,text,delka_bajtu,poradi_spojeni,cast_a,cast_b,cetnost'];
  const rankOf = new Map();
  merges.forEach(([a, b], r) => { if (!rankOf.has(a + b)) rankOf.set(a + b, r); });
  idToToken.forEach((t, id) => {
    const special = specialTokens.includes(t);
    const r = special ? undefined : rankOf.get(t);
    const mg = r !== undefined ? merges[r] : ['', ''];
    lines.push([
      id, q(t), q(display(id)), special ? '' : t.length,
      r ?? '', q(mg[0]), q(mg[1]),
      r !== undefined && mergeCounts ? (mergeCounts[r] ?? '') : '',
    ].join(','));
  });
  return '﻿' + lines.join('\n');
}
