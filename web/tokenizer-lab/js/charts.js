// Jednoduché grafy na <canvas> bez knihoven: čára (s křížem a popiskem) a sloupce (s popiskem).
// Barvy se čtou z CSS proměnných, takže fungují ve světlém i tmavém režimu.

const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const fmt = new Intl.NumberFormat('cs-CZ');

class BaseChart {
  constructor(canvas, opts = {}) {
    this.canvas = canvas;
    this.opts = opts;
    this.ctx = canvas.getContext('2d');
    this.wrap = canvas.parentElement;
    this.tip = document.createElement('div');
    this.tip.className = 'chart-tip';
    this.tip.hidden = true;
    this.wrap.appendChild(this.tip);
    this.hover = null;
    this.pad = { l: 46, r: 12, t: 12, b: 30 };
    new ResizeObserver(() => this.draw()).observe(this.wrap);
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => this.draw());
    canvas.addEventListener('pointermove', e => this.onMove(e));
    canvas.addEventListener('pointerleave', () => { this.hover = null; this.tip.hidden = true; this.draw(); });
  }
  size() {
    const dpr = window.devicePixelRatio || 1;
    const w = this.wrap.clientWidth, h = this.canvas.clientHeight || 200;
    if (this.canvas.width !== Math.round(w * dpr) || this.canvas.height !== Math.round(h * dpr)) {
      this.canvas.width = Math.round(w * dpr); this.canvas.height = Math.round(h * dpr);
    }
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { w, h };
  }
  showTip(html, x, y) {
    this.tip.innerHTML = html;
    this.tip.hidden = false;
    const tw = this.tip.offsetWidth;
    const left = Math.min(Math.max(0, x + 12), this.wrap.clientWidth - tw - 4);
    this.tip.style.left = left + 'px';
    this.tip.style.top = Math.max(0, y - 40) + 'px';
  }
  axes(w, h, yMax, yTicks, yFmt) {
    const { ctx, pad } = this;
    ctx.font = '12px system-ui, sans-serif';
    ctx.strokeStyle = css('--grid');
    ctx.fillStyle = css('--text-muted');
    ctx.lineWidth = 1;
    ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    for (let i = 0; i <= yTicks; i++) {
      const v = (yMax * i) / yTicks;
      const y = h - pad.b - ((h - pad.t - pad.b) * i) / yTicks;
      ctx.beginPath(); ctx.moveTo(pad.l, Math.round(y) + 0.5); ctx.lineTo(w - pad.r, Math.round(y) + 0.5); ctx.stroke();
      ctx.fillText(yFmt(v), pad.l - 6, y);
    }
  }
}

export class LineChart extends BaseChart {
  constructor(canvas, opts) { super(canvas, opts); this.points = []; }
  setData(points) { this.points = points; this.draw(); }
  scales(w, h) {
    const pts = this.points;
    const xMax = Math.max(1, pts.length ? pts[pts.length - 1][0] : 1);
    let yMax = 0; for (const p of pts) yMax = Math.max(yMax, p[1]);
    yMax = Math.max(1, Math.ceil(yMax * 1.15 * 2) / 2);
    const { pad } = this;
    return {
      xMax, yMax,
      x: v => pad.l + ((w - pad.l - pad.r) * v) / xMax,
      y: v => h - pad.b - ((h - pad.t - pad.b) * v) / yMax,
    };
  }
  draw() {
    const { w, h } = this.size();
    const { ctx, pad, points } = this;
    ctx.clearRect(0, 0, w, h);
    const s = this.scales(w, h);
    this.axes(w, h, s.yMax, 4, v => v.toFixed(1));
    ctx.fillStyle = css('--text-muted');
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    for (let i = 0; i <= 4; i++) {
      const v = Math.round((s.xMax * i) / 4);
      ctx.fillText(fmt.format(v), s.x(v), h - pad.b + 8);
    }
    if (!points.length) {
      ctx.fillText(this.opts.empty || 'Zatím žádná data', w / 2, h / 2 - 10);
      return;
    }
    ctx.strokeStyle = css('--series-1');
    ctx.lineWidth = 2; ctx.lineJoin = 'round'; ctx.lineCap = 'round';
    ctx.beginPath();
    points.forEach((p, i) => (i ? ctx.lineTo(s.x(p[0]), s.y(p[1])) : ctx.moveTo(s.x(p[0]), s.y(p[1]))));
    ctx.stroke();
    // poslední bod + přímý popisek
    const last = points[points.length - 1];
    ctx.fillStyle = css('--series-1');
    ctx.beginPath(); ctx.arc(s.x(last[0]), s.y(last[1]), 4, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = css('--text');
    ctx.textAlign = 'right'; ctx.textBaseline = 'bottom';
    ctx.fillText(last[1].toFixed(2), s.x(last[0]) - 6, s.y(last[1]) - 6);
    if (this.hover) {
      const p = this.hover;
      ctx.strokeStyle = css('--text-muted'); ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(s.x(p[0]), pad.t); ctx.lineTo(s.x(p[0]), h - pad.b); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = css('--surface'); ctx.strokeStyle = css('--series-1'); ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(s.x(p[0]), s.y(p[1]), 5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    }
    this.canvas.setAttribute('aria-label',
      `${this.opts.label}: po ${fmt.format(last[0])} spojeních ${last[1].toFixed(2)} znaku na token`);
  }
  onMove(e) {
    if (!this.points.length) return;
    const r = this.canvas.getBoundingClientRect();
    const { w, h } = { w: r.width, h: r.height };
    const s = this.scales(w, h);
    const xv = ((e.clientX - r.left - this.pad.l) / (w - this.pad.l - this.pad.r)) * s.xMax;
    let best = this.points[0];
    for (const p of this.points) if (Math.abs(p[0] - xv) < Math.abs(best[0] - xv)) best = p;
    this.hover = best;
    this.draw();
    this.showTip(`<b>${best[1].toFixed(3)}</b> znaku/token<br><span>po ${fmt.format(best[0])} spojeních</span>`,
      s.x(best[0]), s.y(best[1]));
  }
}

export class BarChart extends BaseChart {
  constructor(canvas, opts) { super(canvas, opts); this.values = []; this.pad.l = 40; }
  setData(values) { this.values = values; this.draw(); }
  layout(w) {
    const n = this.values.length || 1;
    const inner = w - this.pad.l - this.pad.r;
    const slot = inner / n;
    return { slot, bw: Math.max(2, slot - 4) };
  }
  draw() {
    const { w, h } = this.size();
    const { ctx, pad, values } = this;
    ctx.clearRect(0, 0, w, h);
    let max = 0; for (const v of values) max = Math.max(max, v);
    const yMax = Math.max(4, niceMax(max));
    this.axes(w, h, yMax, 4, v => fmt.format(Math.round(v)));
    const { slot, bw } = this.layout(w);
    const labels = this.opts.labels || [];
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    values.forEach((v, i) => {
      const x = pad.l + i * slot + (slot - bw) / 2;
      const bh = ((h - pad.t - pad.b) * v) / yMax;
      const y = h - pad.b - bh;
      ctx.fillStyle = this.hover === i ? css('--series-1-strong') : css('--series-1');
      if (bh > 0) roundTop(ctx, x, y, bw, bh, Math.min(4, bw / 2, bh));
      ctx.fillStyle = css('--text-muted');
      ctx.fillText(labels[i] ?? String(i + 1), x + bw / 2, h - pad.b + 8);
    });
    this.canvas.setAttribute('aria-label', `${this.opts.label}: ` +
      values.map((v, i) => `${labels[i] ?? i + 1}: ${v}`).join(', '));
  }
  onMove(e) {
    if (!this.values.length) return;
    const r = this.canvas.getBoundingClientRect();
    const { slot } = this.layout(r.width);
    const i = Math.floor((e.clientX - r.left - this.pad.l) / slot);
    if (i < 0 || i >= this.values.length) { this.hover = null; this.tip.hidden = true; this.draw(); return; }
    this.hover = i;
    this.draw();
    const lab = (this.opts.labels || [])[i] ?? i + 1;
    this.showTip(`<b>${fmt.format(this.values[i])}</b> tokenů<br><span>${this.opts.unit ? this.opts.unit(lab) : lab}</span>`,
      this.pad.l + (i + 0.5) * slot, e.clientY - r.top);
  }
}

function niceMax(v) {
  if (v <= 0) return 0;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

function roundTop(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x, y + h);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h);
  ctx.closePath();
  ctx.fill();
}
