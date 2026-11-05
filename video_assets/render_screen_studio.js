const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const puppeteer = require('/private/tmp/video-build/node_modules/puppeteer-core');

const FPS = 30;
const DURATION = 10.0;
const TOTAL_FRAMES = Math.round(FPS * DURATION); // 300 frames (10 seconds)
const WIDTH = 1920;
const HEIGHT = 1080;

const HTML_URL = 'http://127.0.0.1:8099/video_assets/screen_studio.html';
const AUDIO_SFX = path.resolve(__dirname, 'screen_studio_sfx.wav');
const OUT_VIDEO_SFX = path.resolve(__dirname, '..', 'bnb_agent_id_screen_studio.mp4');
const OUT_VIDEO_SILENT = path.resolve(__dirname, '..', 'bnb_agent_id_screen_studio_silent.mp4');

async function main() {
  console.log(`=== Screen Studio Video Render ===`);
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
  // Wait for history.json and all data to settle
  await new Promise(r => setTimeout(r, 2000));

  // Spawn FFmpeg for video with subtle tactile SFX
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

  // Also create 100% silent version and localized copy
  console.log('Creating silent MP4 version...');
  const { execSync } = require('child_process');
  execSync(`ffmpeg -y -i "${OUT_VIDEO_SFX}" -an -c:v copy "${OUT_VIDEO_SILENT}"`);
  execSync(`cp "${OUT_VIDEO_SFX}" "${path.resolve(__dirname, 'bnb_agent_id_screen_studio_10s.mp4')}"`);
  execSync(`cp "${OUT_VIDEO_SILENT}" "${path.resolve(__dirname, 'bnb_agent_id_screen_studio_10s_silent.mp4')}"`);

  // Create preview thumbnails
  execSync(`ffmpeg -y -ss 00:00:01.200 -i "${OUT_VIDEO_SFX}" -vframes 1 "${path.resolve(__dirname, 'thumbnail_screen_studio_wide.jpg')}"`);
  execSync(`ffmpeg -y -ss 00:00:05.500 -i "${OUT_VIDEO_SFX}" -vframes 1 "${path.resolve(__dirname, 'thumbnail_screen_studio_modal.jpg')}"`);

  const elapsed = ((Date.now() - tStart) / 1000).toFixed(1);
  const sizeSfx = (fs.statSync(OUT_VIDEO_SFX).size / (1024 * 1024)).toFixed(2);
  const sizeSilent = (fs.statSync(OUT_VIDEO_SILENT).size / (1024 * 1024)).toFixed(2);

  console.log(`\n========================================`);
  console.log(`SUCCESS! Screen Studio video created:`);
  console.log(`Master (tactile UI sound): ${OUT_VIDEO_SFX} (${sizeSfx} MB)`);
  console.log(`Silent Master:             ${OUT_VIDEO_SILENT} (${sizeSilent} MB)`);
  console.log(`Total Render Time:         ${elapsed}s`);
  console.log(`========================================\n`);
}

main().catch((err) => {
  console.error('Fatal error during render:', err);
  process.exit(1);
});
