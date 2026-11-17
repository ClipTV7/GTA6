// Rend une page explainer image par image (window.render(t)) et envoie les images à FFmpeg.
// node render_frames.js <page.html> <sortie.mp4> <durée_s>            -> vidéo muette 30 fps
// node render_frames.js <page.html> <sortie_%t.jpg> 0 stills:0,1.5,4  -> captures fixes (contrôle)
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const fs = require('fs');
(async () => {
  const [html, out, dur, mode] = process.argv.slice(2);
  const fps = 30;
  const browser = await chromium.launch({ args: ['--force-color-profile=srgb', '--font-render-hinting=none'] });
  const page = await browser.newPage({ viewport: { width: 1080, height: 1920 }, deviceScaleFactor: 1 });
  page.on('pageerror', e => { console.error('PAGE ERROR', e.message); process.exitCode = 2; });
  await page.goto('file://' + html);
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all([...document.images].map(i => i.decode().catch(() => {})));
    if (window.layout) window.layout();  // mise en page recalculée avec les vraies polices
  });
  const cdp = await page.context().newCDPSession(page);
  const shot = async (q) => Buffer.from((await cdp.send('Page.captureScreenshot',
    { format: 'jpeg', quality: q, optimizeForSpeed: true })).data, 'base64');
  if (mode && mode.startsWith('stills:')) {
    for (const t of mode.slice(7).split(',').map(Number)) {
      await page.evaluate(t => window.render(t), t);
      fs.writeFileSync(out.replace('%t', t.toFixed(2)), await shot(88));
    }
    await browser.close();
    return;
  }
  const n = Math.ceil(parseFloat(dur) * fps);
  const ff = spawn('ffmpeg', ['-y', '-v', 'error', '-f', 'image2pipe', '-c:v', 'mjpeg', '-framerate', String(fps), '-i', '-',
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p', '-r', String(fps), out],
    { stdio: ['pipe', 'inherit', 'inherit'] });
  for (let f = 0; f < n; f++) {
    await page.evaluate(t => window.render(t), f / fps);
    if (!ff.stdin.write(await shot(93))) await new Promise(r => ff.stdin.once('drain', r));
    if (f % 150 === 0) console.log(`  image ${f}/${n}`);
  }
  ff.stdin.end();
  await new Promise(r => ff.on('close', r));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
