// Erzeugt die PNG-Icons und favicon.ico aus den SVG unter devflow2/static/logo/ (S4.1).
// Aufruf: node ops/icons_erzeugen.cjs <playwright-core>
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.argv[2]);

const static_ = path.join(__dirname, '..', 'devflow2', 'static');
const ziele = [
  ['devflow-icon-any', 192, 'icon-192-any'], ['devflow-icon-any', 512, 'icon-512-any'],
  ['devflow-icon-maskable', 192, 'icon-192-maskable'], ['devflow-icon-maskable', 512, 'icon-512-maskable'],
  ['devflow-badge', 96, 'badge-96'],
  ['devflow-signet', 16, 'favicon-16'], ['devflow-signet', 32, 'favicon-32'], ['devflow-signet', 48, 'favicon-48'],
];

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  fs.mkdirSync(path.join(static_, 'icons'), { recursive: true });
  const png = {};
  for (const [quelle, px, name] of ziele) {
    const svg = fs.readFileSync(path.join(static_, 'logo', `${quelle}.svg`), 'utf8');
    await page.setViewportSize({ width: px, height: px });
    await page.setContent(`<style>html,body{margin:0;background:transparent}svg{display:block;width:${px}px;height:${px}px}</style>${svg}`);
    png[name] = await page.screenshot({ omitBackground: true, clip: { x: 0, y: 0, width: px, height: px } });
    fs.writeFileSync(path.join(static_, 'icons', `${name}.png`), png[name]);
  }
  await browser.close();
  // favicon.ico mit 16, 32 und 48 px (PNG eingebettet)
  const teile = [16, 32, 48].map((px) => png[`favicon-${px}`]);
  const kopf = Buffer.alloc(6 + 16 * teile.length);
  kopf.writeUInt16LE(1, 2); kopf.writeUInt16LE(teile.length, 4);
  let offset = kopf.length;
  teile.forEach((daten, i) => {
    const px = [16, 32, 48][i];
    const e = 6 + 16 * i;
    kopf[e] = px; kopf[e + 1] = px; kopf.writeUInt16LE(1, e + 4); kopf.writeUInt16LE(32, e + 6);
    kopf.writeUInt32LE(daten.length, e + 8); kopf.writeUInt32LE(offset, e + 12);
    offset += daten.length;
  });
  fs.writeFileSync(path.join(static_, 'favicon.ico'), Buffer.concat([kopf, ...teile]));
})();
