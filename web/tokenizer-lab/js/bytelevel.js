// Bytově-úrovňová předtokenizace stejná jako `pre_tokenizers.ByteLevel(add_prefix_space=False)`
// z knihovny Hugging Face `tokenizers` (GPT-2 regex + mapování bajtů na tisknutelné znaky).

export const EOT = '<|endoftext|>';

/** bajt (0–255) → znak, a zpět (tabulka bytes_to_unicode z GPT-2) */
export const BYTE_TO_CHAR = new Array(256);
export const CHAR_TO_BYTE = new Map();
(() => {
  const bs = [];
  for (let i = 33; i <= 126; i++) bs.push(i);
  for (let i = 161; i <= 172; i++) bs.push(i);
  for (let i = 174; i <= 255; i++) bs.push(i);
  const cs = bs.slice();
  let n = 0;
  for (let b = 0; b < 256; b++) {
    if (!bs.includes(b)) { bs.push(b); cs.push(256 + n); n++; }
  }
  bs.forEach((b, i) => {
    const c = String.fromCodePoint(cs[i]);
    BYTE_TO_CHAR[b] = c;
    CHAR_TO_BYTE.set(c, b);
  });
})();

/** Výchozí abeceda: 256 znaků seřazených podle kódu (tak je řadí trenér v `tokenizers`). */
export const ALPHABET = BYTE_TO_CHAR.slice().sort((a, b) => a.codePointAt(0) - b.codePointAt(0));

// GPT-2 vzor, který používá ByteLevel(use_regex=True)
const PRETOKEN_SRC = "'s|'t|'re|'ve|'m|'ll|'d| ?\\p{L}+| ?\\p{N}+| ?[^\\s\\p{L}\\p{N}]+|\\s+(?!\\S)|\\s+";

/** Rozdělí text na „slova“ (před převodem na bajty). */
export function pretokenize(text) {
  const re = new RegExp(PRETOKEN_SRC, 'gu');
  return text.match(re) || [];
}

const utf8 = new TextEncoder();
const utf8dec = new TextDecoder('utf-8', { fatal: false });

/** Řetězec → bytově-úrovňový řetězec (každý bajt UTF-8 jako jeden znak). */
export function toByteLevel(str) {
  const bytes = utf8.encode(str);
  let s = '';
  for (let i = 0; i < bytes.length; i++) s += BYTE_TO_CHAR[bytes[i]];
  return s;
}

/** Bytově-úrovňový token → pole bajtů. */
export function tokenBytes(tok) {
  const out = [];
  for (const ch of tok) {
    const b = CHAR_TO_BYTE.get(ch);
    if (b !== undefined) out.push(b);
  }
  return out;
}

export function bytesToString(bytes) {
  return utf8dec.decode(new Uint8Array(bytes));
}

/** Rozdělí text podle speciálního tokenu: [{text}|{special}] */
export function splitSpecial(text, specials = [EOT]) {
  const parts = [];
  if (!specials.length) return [{ text }];
  const re = new RegExp(specials.map(s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|'), 'g');
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push({ text: text.slice(last, m.index) });
    parts.push({ special: m[0] });
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push({ text: text.slice(last) });
  return parts;
}

/** Počet znaků (kódových bodů), ne UTF-16 jednotek. */
export function charCount(s) {
  let n = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c < 0xdc00 || c > 0xdfff) n++;
  }
  return n;
}
