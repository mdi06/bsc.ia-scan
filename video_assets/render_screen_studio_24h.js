const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const puppeteer = require('/private/tmp/video-build/node_modules/puppeteer-core');

const FPS = 30;
const DURATION = 10.0;
const TOTAL_FRAMES = Math.round(FPS * DURATION); // 300 frames (10 seconds)
const WIDTH = 1920;
const HEIGHT = 1080;

const HTML_URL = 'http://127.0.0.1:8099/video_assets/screen_studio_24h.html';
const AUDIO_SFX = path.resolve(__dirname, 'screen_studio_sfx_24h.wav');
const OUT_VIDEO_SFX = path.resolve(__dirname, '..', 'bnb_agent_id_24h_pulse.mp4');
const OUT_VIDEO_SILENT = path.resolve(__dirname, '..', 'bnb_agent_id_24h_pulse_silent.mp4');
const OUT_THUMBNAIL = path.resolve(__dirname, 'thumbnail_24h_pulse.jpg');

async function main() {
  console.log(`=== 24H Overview 10s Screen Studio Video Render ===`);
  console.log(`Frames: ${TOTAL_FRAMES} @ ${FPS}fps (Duration: ${DURATION}s)`);
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
  console.log(`Loading ${HTML_URL}...`);
  await page.goto(HTML_URL, { waitUntil: 'networkidle0' });
  // Wait for history.json and all data to settle inside the iframe
  await new Promise(r => setTimeout(r, 2500));

  // Spawn FFmpeg for video with tactile SFX
  const ffmpegArgs = [
    '-y',
    '-f', 'image2pipe',
    '-vcodec', 'mjpeg',
    '-r', String(FPS),
    '-i', '-',                      // Frames pipe
    '-i', AUDIO_SFX,                // Tactile SFX
    '-c:v', 'libx264',
    '-pix_fmt', 'yuv420p',
    '-preset', 'fast',
    '-crf', '17',
    '-c:a', 'aac',
    '-b:a', '192k',
    '-shortest',
    OUT_VIDEO_SFX
  ];

  const ffmpeg = spawn('ffmpeg', ffmpegArgs);

  ffmpeg.on('error', (err) => {
    console.error('FFmpeg process error:', err);
  });

  console.log('Rendering frames...');
  for (let f = 0; f < TOTAL_FRAMES; f++) {
    const t = f / FPS;
    await page.evaluate((time) => window.setVideoTime(time), t);

    const buffer = await page.screenshot({
      type: 'jpeg',
      quality: 94,
      clip: { x: 0, y: 0, width: WIDTH, height: HEIGHT }
    });

    // Save keyframe thumbnail at t=2.0s (Card 1 zoom) or t=0.5s
    if (f === 60) {
      fs.writeFileSync(OUT_THUMBNAIL, buffer);
    }

    const canContinue = ffmpeg.stdin.write(buffer);
    if (!canContinue) {
      await new Promise((resolve) => ffmpeg.stdin.once('drain', resolve));
    }

    if (f % 60 === 0 || f === TOTAL_FRAMES - 1) {
      const elapsed = ((Date.now() - tStart) / 1000).toFixed(1);
      const pct = Math.round((f / TOTAL_FRAMES) * 100);
      console.log(`Frame ${f}/${TOTAL_FRAMES} (${pct}%) - ${t.toFixed(2)}s / ${DURATION}s [${elapsed}s elapsed]`);
    }
  }

  ffmpeg.stdin.end();

  await new Promise((resolve, reject) => {
    ffmpeg.on('close', (code) => {
      if (code === 0) resolve();
      else reject(new Error(`FFmpeg exited with code ${code}`));
    });
  });

  console.log(`Video with SFX rendered to: ${OUT_VIDEO_SFX}`);

  // Generate silent video version for platforms that mute or preview
  console.log('Generating silent video...');
  await new Promise((resolve, reject) => {
    const silent = spawn('ffmpeg', [
      '-y',
      '-i', OUT_VIDEO_SFX,
      '-an',
      '-c:v', 'copy',
      OUT_VIDEO_SILENT
    ]);
    silent.on('close', (code) => {
      if (code === 0) resolve();
      else reject(new Error(`Silent FFmpeg exited with code ${code}`));
    });
  });

  console.log(`Silent video rendered to: ${OUT_VIDEO_SILENT}`);

  await browser.close();
  const totalSec = ((Date.now() - tStart) / 1000).toFixed(1);
  console.log(`✅ All renders finished in ${totalSec}s`);
}

main().catch(err => {
  console.error('Fatal error during render:', err);
  process.exit(1);
});

