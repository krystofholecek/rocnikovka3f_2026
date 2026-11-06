// Web Worker: statistika nahraných souborů a demonstrační výpočet pipeline „prepare“.
import { scanFile, iterDocs } from './docs.js';
import { BpeTokenizer } from './bpe.js';
import { EOT, charCount } from './bytelevel.js';

// Deterministický generátor (seed 2005 jako v prepare.py; samotná čísla se od Pythonu liší).
function mulberry32(a) {
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

let pipeRun = 0;

async function pipeline(msg) {
  const my = ++pipeRun;
  const tok = new BpeTokenizer(msg.tok);
  const eot = tok.tokenToId.get(EOT);
  const rng = mulberry32(2005);
  const budget = (msg.maxMB || Infinity) * 1048576;
  const r = {
    docs: 0, chars: 0, bytes: 0,
    train: { docs: 0, tokens: 0 }, val: { docs: 0, tokens: 0 },
    preview: [], truncated: false, hasEot: eot !== undefined,
  };
  const t0 = performance.now();
  let lastPost = 0, docTokens = 0, docPreview = [];
  const totalSize = msg.files ? msg.files.reduce((s, f) => s + Math.min(f.size, budget), 0) : 1;
  let doneBytes = 0;

  const endDoc = () => {
    const split = rng() < msg.valFraction ? 'val' : 'train';
    const n = docTokens + (eot !== undefined ? 1 : 0);
    r[split].docs++; r[split].tokens += n; r.docs++;
    if (r.preview.length < 3) r.preview.push({ ids: docPreview, total: n, split });
    docTokens = 0; docPreview = [];
  };
  const feed = text => {
    const { ids } = tok.encode(text);
    docTokens += ids.length;
    r.chars += charCount(text);
    if (r.preview.length < 3 && docPreview.length < 40) docPreview.push(...ids.slice(0, 40 - docPreview.length));
  };
  const maybePost = frac => {
    const now = performance.now();
    if (now - lastPost < 100) return;
    lastPost = now;
    self.postMessage({ type: 'pipeProgress', frac, r: { ...r, preview: [] }, ms: now - t0 });
  };

  if (msg.texts) {
    msg.texts.forEach((t, i) => { feed(t); endDoc(); maybePost((i + 1) / msg.texts.length); });
    r.bytes = msg.texts.reduce((s, t) => s + new TextEncoder().encode(t).length, 0);
  } else {
    for (const f of msg.files) {
      const left = budget - doneBytes;
      if (left <= 0) { r.truncated = true; break; }
      if (f.size > left) r.truncated = true;
      const base = doneBytes;
      for await (const { text, end } of iterDocs(f, {
        field: msg.field, maxBytes: left,
        onBytes: b => { doneBytes = base + b; maybePost(doneBytes / totalSize); },
      })) {
        if (my !== pipeRun) return;
        if (text) feed(text);
        if (end) endDoc();
      }
      if (docTokens) endDoc();
      doneBytes = base + Math.min(f.size, left);
    }
    r.bytes = doneBytes;
  }
  self.postMessage({ type: 'pipeDone', r, ms: performance.now() - t0 });
}

self.onmessage = async e => {
  const msg = e.data;
  try {
    if (msg.type === 'scan') {
      const res = await scanFile(msg.file, {
        field: msg.field,
        onProgress: frac => self.postMessage({ type: 'scanProgress', id: msg.id, frac }),
      });
      self.postMessage({ type: 'scanned', id: msg.id, ...res });
    } else if (msg.type === 'pipeline') {
      await pipeline(msg);
    } else if (msg.type === 'cancelPipeline') {
      pipeRun++;
    }
  } catch (err) {
    self.postMessage({ type: 'error', id: msg.id, message: String(err && err.message || err) });
  }
};
