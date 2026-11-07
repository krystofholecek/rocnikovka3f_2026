// Ruční/automatické ověření v prohlížeči (Playwright + nainstalovaný Chrome).
//   1) python -m http.server 8765   2) node tests/e2e.mjs [http://127.0.0.1:8765/]
// Ukládá screenshoty do tests/out/ a měří, jestli stránka nezamrzá při 50 MB souboru.
import { chromium } from 'playwright';
import { mkdirSync, existsSync, statSync, createWriteStream } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { SAMPLE_DOCS } from '../js/sample.js';

const URL_ = process.argv[2] || 'http://127.0.0.1:8765/';
const OUT = path.join(path.dirname(fileURLToPath(import.meta.url)), 'out');
mkdirSync(OUT, { recursive: true });
const shot = (page, name) => page.screenshot({ path: path.join(OUT, name), fullPage: true });
const results = [];
const check = (name, ok, info = '') => { results.push({ name, ok, info }); console.log(`${ok ? '✔' : '✘'} ${name}${info ? ' – ' + info : ''}`); };

// 50 MB testovací JSONL (syntetický text složený z ukázky + čísel)
const big = path.join(OUT, 'big-50mb.jsonl');
if (!existsSync(big) || statSync(big).size < 50e6) {
  const ws = createWriteStream(big);
  let size = 0, i = 0;
  while (size < 50 * 1048576) {
    const line = JSON.stringify({ text: `${SAMPLE_DOCS[i % SAMPLE_DOCS.length]} (#${i}, ${i * 7 % 1000})`, id: i }) + '\n';
    ws.write(line); size += Buffer.byteLength(line); i++;
  }
  await new Promise(r => ws.end(r));
}

const browser = await chromium.launch({ channel: 'chrome' });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e)));
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });

await page.goto(URL_);
await page.evaluate(() => localStorage.clear());
await page.reload();
await shot(page, '01-data.png');

// --- trénink na ukázce
await page.click('#sampleBtn');
await page.click('#toTrainBtn');
await page.fill('#vocabInput', '1000');
await page.click('#startBtn');
await page.waitForFunction(() => document.querySelector('#trainMsg').textContent.includes('Hotovo'), null, { timeout: 60000 });
check('trénink na ukázce doběhl', true, await page.textContent('#stMerges'));
await page.mouse.move(700, 300);
await shot(page, '02-trenink.png');

// --- krokování
await page.click('#resetBtn');
await page.waitForFunction(() => document.querySelector('#startBtn').textContent.includes('Start'));
for (let i = 0; i < 3; i++) {
  await page.click('#stepBtn');
  await page.waitForFunction(n => document.querySelector('#stepCaption').textContent.includes(`č. ${n}`), i + 1, { timeout: 15000 });
}
check('krokování po jednom spojení', true, await page.textContent('#stepCaption'));
await page.waitForTimeout(300);
await page.locator('.step-card').screenshot({ path: path.join(OUT, '03-krok-zvyrazneni.png') });
await page.waitForTimeout(1000);
await page.locator('.step-card').screenshot({ path: path.join(OUT, '03b-krok-po-spojeni.png') });

// dotrénovat a stáhnout tokenizer.json
await page.click('#resumeBtn');
await page.waitForFunction(() => document.querySelector('#trainMsg').textContent.includes('Hotovo'), null, { timeout: 60000 });
const [dl] = await Promise.all([page.waitForEvent('download'), page.click('#dlJsonBtn')]);
await dl.saveAs(path.join(OUT, 'web-tokenizer.json'));
check('stažení tokenizer.json', true, path.join(OUT, 'web-tokenizer.json'));

// --- prohlížeč
await page.click('#tab-view');
await page.click('#decodeBtn');
const dec = await page.textContent('#decodeResult');
check('dekódování zpět beze ztráty', dec.includes('✓'), dec.trim());
await page.hover('#viewTokens .tk:nth-child(3)');
await shot(page, '04-prohlizec.png');
await page.click('[data-merges="100"]');
await shot(page, '04b-prohlizec-100.png');

// --- pipeline
await page.click('#tab-pipe');
await page.click('#pipeBtn');
await page.waitForFunction(() => document.querySelector('#pipeMsg').textContent.includes('Hotovo'), null, { timeout: 60000 });
check('pipeline na ukázce', true, await page.textContent('#fSplit'));
await shot(page, '05-pipeline.png');

// --- 50 MB soubor: měření nejdelší pauzy hlavního vlákna
await page.click('#tab-data');
await page.evaluate(() => {
  window.__gaps = [];
  let last = performance.now();
  const loop = t => { window.__gaps.push(t - last); last = t; window.__raf = requestAnimationFrame(loop); };
  window.__raf = requestAnimationFrame(loop);
});
const t0 = Date.now();
await page.setInputFiles('#fileInput', big);
await page.waitForFunction(() => document.querySelector('#fileRows').textContent.includes('připraveno') &&
  !document.querySelector('#fileRows').textContent.includes('čtu'), null, { timeout: 120000 });
const scanS = (Date.now() - t0) / 1000;
await shot(page, '06-data-50mb.png');
await page.click('#toTrainBtn');
await page.fill('#vocabInput', '2000');
await page.fill('#sampleMBInput', '50');
await page.click('#startBtn');
await page.waitForFunction(() => document.querySelector('#trainMsg').textContent.includes('Hotovo'), null, { timeout: 300000 });
const gaps = await page.evaluate(() => { cancelAnimationFrame(window.__raf); return window.__gaps; });
const maxGap = Math.max(...gaps.slice(2));
check('50 MB: čtení + trénink nezamrzne UI', maxGap < 250, `sken ${scanS.toFixed(1)} s, nejdelší snímek ${maxGap.toFixed(0)} ms, ${await page.textContent('#stTime')}`);
await shot(page, '07-trenink-50mb.png');

// --- tmavý režim + mobil
await page.emulateMedia({ colorScheme: 'dark' });
await page.click('#tab-view');
await shot(page, '08-tmavy.png');
const mob = await browser.newPage({ viewport: { width: 390, height: 844 } });
await mob.goto(URL_);
await mob.click('#sampleBtn');
const overflow = await mob.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
check('mobil bez vodorovného posunu', !overflow);
await mob.screenshot({ path: path.join(OUT, '09-mobil.png'), fullPage: true });

check('bez chyb v konzoli', errors.length === 0, errors.join(' | '));
await browser.close();
const failed = results.filter(r => !r.ok).length;
console.log(failed ? `\n${failed} kontrol selhalo` : '\nVše OK');
process.exit(failed ? 1 : 0);
