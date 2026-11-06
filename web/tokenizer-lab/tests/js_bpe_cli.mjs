// Pomocník pro křížovou kontrolu: stdin JSON {docs, vocab, sentences} → stdout JSON.
import { BpeTrainer, BpeTokenizer, countWords } from '../js/bpe.js';

let input = '';
process.stdin.setEncoding('utf8');
for await (const c of process.stdin) input += c;
const { docs, vocab, sentences } = JSON.parse(input);

const counts = new Map();
for (const d of docs) countWords(d, counts);
const t = new BpeTrainer(counts, { vocabSize: vocab });
while (t.step());
const snap = t.snapshot();
const tok = new BpeTokenizer(snap);
process.stdout.write(JSON.stringify({
  vocab: snap.idToToken,
  merges: snap.merges,
  counts: snap.mergeCounts,
  encoded: sentences.map(s => tok.encode(s).ids),
}));
