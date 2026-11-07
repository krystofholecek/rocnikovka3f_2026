import { test } from 'node:test';
import assert from 'node:assert/strict';
import { BpeTrainer, BpeTokenizer, countWords, toHfJson, fromHfJson } from '../js/bpe.js';
import { pretokenize, toByteLevel, BYTE_TO_CHAR, ALPHABET, EOT } from '../js/bytelevel.js';
import { SAMPLE_DOCS, SAMPLE_TEXT } from '../js/sample.js';

function train(docs, vocabSize) {
  const counts = new Map();
  for (const d of docs) countWords(d, counts);
  const t = new BpeTrainer(counts, { vocabSize });
  while (t.step());
  return t;
}

test('bajtová abeceda má 256 různých znaků a mezera je Ġ', () => {
  assert.equal(new Set(BYTE_TO_CHAR).size, 256);
  assert.equal(ALPHABET.length, 256);
  assert.equal(BYTE_TO_CHAR[32], 'Ġ');
  assert.equal(BYTE_TO_CHAR[10], 'Ċ');
  assert.equal(toByteLevel(' č'), 'ĠÄį');
});

test('předtokenizace jako GPT-2', () => {
  assert.deepEqual(pretokenize("Hello world, it's 2005!"), ['Hello', ' world', ',', ' it', "'s", ' 2005', '!']);
  assert.deepEqual(pretokenize('a  b'), ['a', ' ', ' b']);
});

test('malý korpus: očekávaná první spojení', () => {
  const t = train(['that this the thin thug'], 300);
  const m = t.merges.map(x => t.idToToken[x.a] + ' + ' + t.idToToken[x.b]);
  // "th" (5×) je nejčastější dvojice, pak "Ġ"+"th" (4×)
  assert.equal(m[0], 't + h');
  assert.equal(m[1], 'Ġ + th');
  assert.equal(t.merges[0].count, 5);
  assert.equal(t.idToToken[0], EOT);
  assert.equal(t.idToToken.length, 257 + t.merges.length);
});

test('shoda četností: vyhrává dvojice s menšími ID', () => {
  // "ab" i "cd" mají četnost 1; a(65) < c(67) → první je a+b
  const t = train(['ab cd'], 260);
  assert.equal(t.idToToken[t.merges[0].a], 'a');
});

test('round-trip encode/decode bez ztráty (čeština, emoji, mezery)', () => {
  const t = train(SAMPLE_DOCS, 600);
  const tok = new BpeTokenizer(t.snapshot());
  const samples = [SAMPLE_TEXT, 'Žluťoučký kůň úpěl ďábelské ódy 🐴  \n\t konec', `a${EOT}b`, ''];
  for (const s of samples) {
    const { ids } = tok.encode(s);
    assert.equal(tok.decode(ids), s);
  }
  assert.ok(tok.encode(SAMPLE_TEXT).ids.length < new TextEncoder().encode(SAMPLE_TEXT).length / 2);
});

test('enkódování trénovacích slov odpovídá stavu trenéra', () => {
  const t = train(SAMPLE_DOCS, 500);
  const tok = new BpeTokenizer(t.snapshot());
  let total = 0;
  for (const d of SAMPLE_DOCS) total += tok.encode(d).ids.length;
  assert.equal(total, t.totalTokens);
});

test('maxMerges omezuje rozdělení (0 spojení = bajty)', () => {
  const t = train(SAMPLE_DOCS, 800);
  const tok = new BpeTokenizer(t.snapshot());
  const s = 'the weather';
  assert.equal(tok.encode(s, { maxMerges: 0 }).ids.length, 11);
  const a = tok.encode(s, { maxMerges: 50 }).ids.length;
  const b = tok.encode(s).ids.length;
  assert.ok(a >= b && a < 11);
});

test('export/import tokenizer.json zachová tokenizaci', () => {
  const t = train(SAMPLE_DOCS, 700);
  const snap = t.snapshot();
  const back = fromHfJson(toHfJson(snap));
  assert.deepEqual(back.idToToken, snap.idToToken);
  assert.deepEqual(back.merges, snap.merges);
  const a = new BpeTokenizer(snap), b = new BpeTokenizer(back);
  assert.deepEqual(a.encode(SAMPLE_TEXT).ids, b.encode(SAMPLE_TEXT).ids);
  // starší formát merges jako "a b"
  const obj = JSON.parse(toHfJson(snap));
  obj.model.merges = obj.model.merges.map(([x, y]) => `${x} ${y}`);
  assert.deepEqual(fromHfJson(obj).merges, snap.merges);
});
