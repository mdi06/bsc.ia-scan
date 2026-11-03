const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const puppeteer = require('/private/tmp/video-build/node_modules/puppeteer-core');

const FPS = 30;
const DURATION = 18.0;
const TOTAL_FRAMES = Math.round(FPS * DURATION); // 540 frames
const WIDTH = 1920;
const HEIGHT = 1080;

const HTML_PATH = path.resolve(__dirname, 'renderer.html');
const AUDIO_PATH = path.resolve(__dirname, 'master_audio.wav');
const OUTPUT_VIDEO = path.resolve(__dirname, '..', 'bnb_agent_id_suite_demo_1080p.mp4');

async function main() {
  console.log(`Starting video render: ${TOTAL_FRAMES} frames @ ${FPS}fps (${WIDTH}x${HEIGHT})`);
  const tStart = Date.now();

  const browser = await puppeteer.launch({
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    headless: 'new',
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
      '--font-render-hinting=none'
    ]
  });

  const page = await browser.newPage();
  await page.setViewport({ width: WIDTH, height: HEIGHT, deviceScaleFactor: 1 });
  await page.goto(`file://${HTML_PATH}`, { waitUntil: 'load' });

  // Spawn FFmpeg to stream frames directly into H.264 MP4
  const ffmpegArgs = [
    '-y',
    '-f', 'image2pipe',
    '-vcodec', 'mjpeg',
    '-r', String(FPS),
    '-i', '-',                      // Video from pipe
    '-i', AUDIO_PATH,               // Audio file
    '-c:v', 'libx264',
    '-pix_fmt', 'yuv420p',
    '-preset', 'fast',
    '-crf', '18',
    '-c:a', 'aac',
    '-b:a', '192k',
    '-shortest',
    OUTPUT_VIDEO
  ];

  const ffmpeg = spawn('ffmpeg', ffmpegArgs);

  ffmpeg.stderr.on('data', (data) => {
    // optional debug logging
  });

  ffmpeg.on('error', (err) => {
    console.error('FFmpeg process error:', err);
  });

  console.log('Rendering frames...');
  for (let f = 0; f < TOTAL_FRAMES; f++) {
    const t = f / FPS;
    await page.evaluate((time) => window.setVideoTime(time), t);

    const buffer = await page.screenshot({
      type: 'jpeg',
      quality: 92,
      clip: { x: 0, y: 0, width: WIDTH, height: HEIGHT }
    });

    const canContinue = ffmpeg.stdin.write(buffer);
    if (!canContinue) {
      await new Promise((resolve) => ffmpeg.stdin.once('drain', resolve));
    }

    if (f % 60 === 0 || f === TOTAL_FRAMES - 1) {
      const progress = ((f / TOTAL_FRAMES) * 100).toFixed(1);
      const elapsed = ((Date.now() - tStart) / 1000).toFixed(1);
      console.log(`Frame ${f}/${TOTAL_FRAMES} (${progress}%) - Elapsed: ${elapsed}s`);
    }
  }

  ffmpeg.stdin.end();

  await new Promise((resolve, reject) => {
    ffmpeg.on('close', (code) => {
      if (code === 0) resolve();
      else reject(new Error(`FFmpeg exited with code ${code}`));
    });
  });

  await browser.close();

  const totalTime = ((Date.now() - tStart) / 1000).toFixed(1);
  const stats = fs.statSync(OUTPUT_VIDEO);
  const fileSizeMb = (stats.size / (1024 * 1024)).toFixed(2);

  console.log(`\n========================================`);
  console.log(`SUCCESS! Video created at:`);
  console.log(`${OUTPUT_VIDEO}`);
  console.log(`Size: ${fileSizeMb} MB`);
  console.log(`Render time: ${totalTime}s`);
  console.log(`========================================\n`);
}

main().catch((err) => {
  console.error('Fatal error during render:', err);
  process.exit(1);
});

