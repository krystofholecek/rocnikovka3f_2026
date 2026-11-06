// Web Worker: trénink BPE mimo hlavní vlákno. UI dostává souhrn nejvýš ~10× za sekundu.
import { BpeTrainer, countWords } from './bpe.js';
import { iterDocs } from './docs.js';

let trainer = null;
let state = 'idle';        // idle | reading | paused | running | done
let runId = 0;
let sentMerges = 0, sentTokens = 0;
let activeMs = 0, runStart = 0, lastPost = 0;
let history = [];
let info = {};

const now = () => performance.now();
const yieldEvents = (() => {
  const ch = new MessageChannel();
  const q = [];
  ch.port1.onmessage = () => q.shift()?.();
  return () => new Promise(r => { q.push(r); ch.port2.postMessage(0); });
})();

function elapsed() { return activeMs + (state === 'running' ? now() - runStart : 0); }

function snapshotMsg(extra = {}) {
  const t = trainer;
  const newMerges = t.merges.slice(sentMerges).map(m => [m.a, m.b, m.id, m.count]);
  const newTokens = t.idToToken.slice(sentTokens);
  sentMerges = t.merges.length; sentTokens = t.idToToken.length;
  record(true);
  const points = history; history = [];
  return {
    type: 'progress', state, info,
    merges: t.merges.length, vocabLen: t.vocabLen, vocabSize: t.vocabSize,
    elapsedMs: elapsed(), cpt: t.charsPerToken, totalTokens: t.totalTokens,
    newMerges, newTokens, points,
    hist: t.lengthHistogram(), longest: t.longestTokens(15),
    ...extra,
  };
}

// body grafu „znaků na token“: zhruba 400 bodů za celý trénink, nezávisle na rychlosti
let lastRecorded = -1;
function record(force = false) {
  const n = trainer.merges.length;
  const every = Math.max(1, Math.floor((trainer.vocabSize - (info.baseVocab || 257)) / 400));
  if (n !== lastRecorded && (force || n % every === 0)) { history.push([n, trainer.charsPerToken]); lastRecorded = n; }
}

function post(extra) { lastPost = now(); self.postMessage(snapshotMsg(extra)); }

async function prepare(msg) {
  const my = ++runId;
  state = 'reading';
  const budget = Math.max(0.01, msg.sampleMB) * 1048576;
  const counts = new Map();
  let used = 0, docs = 0, truncated = false;
  const t0 = now();
  const progress = (frac, label) => {
    if (now() - lastPost < 100) return;
    lastPost = now();
    self.postMessage({ type: 'phase', phase: 'reading', frac, label });
  };
  if (msg.texts) {
    for (const s of msg.texts) {
      if (used >= budget) { truncated = true; break; }
      const part = s.slice(0, Math.max(0, budget - used));
      countWords(part, counts); used += part.length; docs++;
    }
  } else {
    for (const f of msg.files) {
      if (used >= budget) { truncated = true; break; }
      const max = budget - used;
      if (f.size > max) truncated = true;
      const base = used;
      for await (const { text, end } of iterDocs(f, {
        field: msg.field, maxBytes: max,
        onBytes: b => { used = base + b; progress(Math.min(1, used / budget), f.name); },
      })) {
        if (my !== runId) return;
        countWords(text, counts);
        if (end) docs++;
      }
      used = base + Math.min(f.size, max);
    }
  }
  if (my !== runId) return;
  self.postMessage({ type: 'phase', phase: 'init', frac: 1, label: `${counts.size.toLocaleString('cs')} unikátních slov` });
  await yieldEvents();
  trainer = new BpeTrainer(counts, { vocabSize: msg.vocabSize });
  sentMerges = 0; sentTokens = 0; activeMs = 0; history = []; lastRecorded = -1;
  info = {
    sampleBytes: used, docs, truncated, uniqueWords: trainer.uniqueWords,
    totalBytes: trainer.totalBytes, totalChars: trainer.totalChars, readMs: now() - t0,
    baseVocab: trainer.vocabLen,
  };
  state = 'paused';
  post({ init: true });
  if (!msg.paused) run();
  else if (msg.stepAfter) doStep();
}

async function run() {
  if (!trainer || trainer.done) return;
  const my = ++runId;
  state = 'running'; runStart = now();
  post();
  while (state === 'running' && my === runId) {
    const end = now() + 30;
    while (now() < end) { if (!trainer.step()) break; record(); }
    if (trainer.done) { activeMs += now() - runStart; state = 'done'; post({ done: true }); return; }
    if (now() - lastPost >= 100) post();
    await yieldEvents();
  }
}

function pause() {
  if (state !== 'running') return;
  runId++;
  activeMs += now() - runStart;
  state = 'paused';
  post();
}

function doStep() {
  if (!trainer || state === 'done') return;
  if (state === 'running') pause();
  const t = now();
  const m = trainer.step();
  activeMs += now() - t;
  if (!m) { state = 'done'; post({ done: true }); return; }
  post({ stepped: { rank: m.rank, a: m.a, b: m.b, id: m.id, count: m.count } });
}

self.onmessage = e => {
  const msg = e.data;
  switch (msg.type) {
    case 'start':
      prepare(msg).catch(err => self.postMessage({ type: 'error', message: String(err && err.message || err) }));
      break;
    case 'pause': pause(); break;
    case 'resume': run(); break;
    case 'step': doStep(); break;
    case 'reset':
      runId++; trainer = null; state = 'idle'; history = [];
      self.postMessage({ type: 'reset' });
      break;
  }
};
