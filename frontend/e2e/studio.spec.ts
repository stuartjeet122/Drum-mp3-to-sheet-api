import { expect, test, type Page } from '@playwright/test';
import MidiPackage from '@tonejs/midi';

const { Midi } = MidiPackage;

const id = '9d199c08-11bf-4c48-9a7a-aaef1f688531';
const baseJob = {
  job_id: id, status: 'COMPLETED', progress_percent: 100,
  original_filename: 'Sunday pocket.wav', bpm: 92, time_signature: '4/4', duration_sec: 8,
  error_message: null, updated_at: '2026-11-16T10:00:00Z',
  available_exports: { midi: true, musicxml: true, pdf: true },
};

function midiFile() {
  const midi = new Midi();
  midi.header.setTempo(92);
  const track = midi.addTrack(); track.channel = 9;
  for (let index = 0; index < 24; index++) {
    track.addNote({ midi: 42, time: index * 0.326, duration: 0.06, velocity: 0.5 });
    if (index % 2 === 0) track.addNote({ midi: index % 4 === 0 ? 36 : 38, time: index * 0.326, duration: 0.1, velocity: 0.8 });
  }
  track.addNote({ midi: 38, time: 0.652, duration: 0.08, velocity: 0.5 });
  track.addNote({ midi: 46, time: 0.652, duration: 0.4, velocity: 0.65 });
  return Buffer.from(midi.toArray());
}

function pdfFile() {
  const drawing = 'BT /F1 22 Tf 55 720 Td (Sunday pocket) Tj ET\n0.5 w\n' + Array.from({ length: 5 }, (_, index) => `55 ${650 - index * 9} m 545 ${650 - index * 9} l S`).join('\n');
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${Buffer.byteLength(drawing)} >>\nstream\n${drawing}\nendstream`,
  ];
  let content = '%PDF-1.4\n';
  const offsets = [0];
  objects.forEach((object, index) => { offsets.push(Buffer.byteLength(content)); content += `${index + 1} 0 obj\n${object}\nendobj\n`; });
  const xref = Buffer.byteLength(content);
  content += `xref\n0 6\n0000000000 65535 f \n${offsets.slice(1).map(offset => `${String(offset).padStart(10, '0')} 00000 n \n`).join('')}trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
  return Buffer.from(content);
}

async function fixtures(page: Page, list = [baseJob]) {
  await page.route('**/drum-sheet-api/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/export/midi')) return route.fulfill({ contentType: 'audio/midi', body: midiFile() });
    if (path.endsWith('/export/pdf')) return route.fulfill({ contentType: 'application/pdf', body: pdfFile() });
    if (path.endsWith('/export/musicxml')) return route.fulfill({ contentType: 'application/vnd.recordare.musicxml+xml', body: '<score-partwise version="4.0" />' });
    if (path.endsWith('/status')) return route.fulfill({ json: baseJob });
    if (route.request().method() === 'GET') return route.fulfill({ json: list });
    return route.fulfill({ status: 500, json: { detail: 'Unexpected mutation in a read-only test' } });
  });
}

async function canvasColors(page: Page, selector: string) {
  const image = (await page.locator(selector).screenshot()).toString('base64');
  return page.evaluate(async data => {
    const picture = new Image(); picture.src = `data:image/png;base64,${data}`; await picture.decode();
    const sample = document.createElement('canvas'); sample.width = picture.width; sample.height = picture.height;
    const context = sample.getContext('2d')!; context.drawImage(picture, 0, 0);
    const pixels = context.getImageData(0, 0, sample.width, sample.height).data;
    const colors = new Set<number>();
    for (let index = 0; index < pixels.length; index += 16) colors.add((pixels[index] >> 3) * 1024 + (pixels[index + 1] >> 3) * 32 + (pixels[index + 2] >> 3));
    return colors.size;
  }, image);
}

test('desktop studio renders a top-down kit and functional demo controls', async ({ page }, testInfo) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await fixtures(page, []);
  await page.goto('./');
  await expect(page.getByRole('heading', { name: 'Your drum studio.' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Add a recording' })).toBeVisible();
  await page.getByRole('link', { name: 'Play & score' }).click();
  await expect(page.getByRole('heading', { name: 'Play your score.' })).toBeVisible();
  await expect(page.getByRole('group', { name: 'Interactive top view of an acoustic drum kit' })).toBeVisible();
  await expect.poll(() => canvasColors(page, '.top-kit-svg')).toBeGreaterThan(45);
  await page.screenshot({ path: testInfo.outputPath('desktop-studio.png'), fullPage: true });
  await page.getByRole('button', { name: 'Play drums', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Pause playback', exact: true })).toBeVisible();
  await expect.poll(async () => Number(await page.getByRole('slider', { name: 'Playback position' }).inputValue())).toBeGreaterThan(0.3);
  await page.getByRole('button', { name: 'Pause playback', exact: true }).click();
  const paused = Number(await page.getByRole('slider', { name: 'Playback position' }).inputValue());
  await page.getByRole('button', { name: 'Loop playback' }).click();
  await expect(page.getByRole('button', { name: 'Loop playback' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('slider', { name: 'Playback position' })).toHaveValue(String(paused));
  await page.getByRole('button', { name: 'Mute drums' }).click();
  await expect(page.getByRole('button', { name: 'Unmute drums' })).toBeVisible();
  await page.getByRole('combobox', { name: 'Playback speed' }).selectOption('0.75');
  await page.getByRole('button', { name: 'Snare', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Snare', exact: true })).toHaveClass(/is-hit/);
  expect(errors).toEqual([]);
});

test('upload starts once, polls real stages, and displays downloadable PDF/MIDI/XML', async ({ page }, testInfo) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await fixtures(page, []);
  let uploads = 0; let starts = 0; let polls = 0;
  await page.route('**/drum-sheet-api/api/transcriptions', async route => {
    uploads++;
    expect(route.request().headers()['content-type']).toContain('multipart/form-data');
    await route.fulfill({ status: 201, json: { job_id: id, status: 'UPLOADED', original_filename: 'Sunday pocket.wav', file_size_bytes: 64, created_at: baseJob.updated_at } });
  });
  await page.route(`**/transcriptions/${id}/start`, async route => { starts++; await route.fulfill({ status: 202, json: { ...baseJob, status: 'PREPROCESSING', progress_percent: 0, available_exports: { midi: false, musicxml: false, pdf: false } } }); });
  await page.route(`**/transcriptions/${id}/status`, async route => { polls++; await route.fulfill({ json: polls === 1 ? { ...baseJob, status: 'OAF_VERIFICATION', progress_percent: 65, available_exports: { midi: false, musicxml: false, pdf: false } } : baseJob }); });
  await page.goto('./');
  await page.getByLabel('Choose drum recording').setInputFiles({ name: 'Sunday pocket.wav', mimeType: 'audio/wav', buffer: Buffer.from('RIFF0000WAVEdata') });
  await page.getByRole('button', { name: 'Create my score' }).click();
  await expect(page.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '65');
  await expect(page.getByRole('heading', { name: 'Your groove, on paper.' })).toBeVisible();
  await page.getByRole('link', { name: 'Play & view score' }).click();
  await expect(page.getByRole('link', { name: 'MusicXML', exact: true })).toHaveAttribute('href', /export\/musicxml$/);
  await expect(page.getByRole('link', { name: 'MIDI', exact: true })).toHaveAttribute('href', /export\/midi$/);
  await expect(page.getByRole('img', { name: 'Drum sheet music, page 1' })).toBeVisible();
  await expect.poll(() => canvasColors(page, '.pdf-paper-area canvas')).toBeGreaterThan(3);
  await expect(page.getByRole('heading', { name: 'Sunday pocket', exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('completed-score.png'), fullPage: true });
  await page.getByRole('button', { name: 'Play drums', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Pause playback', exact: true })).toBeVisible();
  await expect(page.locator('.drum-pad.is-hit')).toHaveCount(1);
  await expect.poll(async () => Number(await page.getByRole('slider', { name: 'Playback position' }).inputValue())).toBeGreaterThan(1.3);
  await expect(page.locator('.inline-error')).toHaveCount(0);
  expect(uploads).toBe(1); expect(starts).toBe(1); expect(errors).toEqual([]);
});

test('queue errors allow retrying the existing upload without uploading twice', async ({ page }) => {
  await fixtures(page, []);
  let uploads = 0; let starts = 0;
  await page.route('**/drum-sheet-api/api/transcriptions', async route => { uploads++; await route.fulfill({ status: 201, json: { job_id: id, status: 'UPLOADED', original_filename: 'Sunday pocket.wav', created_at: baseJob.updated_at } }); });
  await page.route(`**/transcriptions/${id}/start`, async route => { starts++; await route.fulfill(starts === 1 ? { status: 503, json: { detail: 'Queue unavailable' } } : { status: 202, json: baseJob }); });
  await page.goto('./');
  await page.getByLabel('Choose drum recording').setInputFiles({ name: 'Sunday pocket.wav', mimeType: 'audio/wav', buffer: Buffer.from('RIFF0000WAVEdata') });
  await page.getByRole('button', { name: 'Create my score' }).click();
  await expect(page.getByRole('alert')).toContainText('Queue unavailable');
  await page.getByRole('button', { name: 'Start transcription', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Your groove, on paper.' })).toBeVisible();
  expect(uploads).toBe(1); expect(starts).toBe(2);
});

test('invalid file is rejected before upload and missing PDF keeps other exports usable', async ({ page }) => {
  await fixtures(page, [{ ...baseJob, available_exports: { midi: true, musicxml: true, pdf: false } }]);
  await page.route(`**/transcriptions/${id}/status`, route => route.fulfill({ json: { ...baseJob, available_exports: { midi: true, musicxml: true, pdf: false } } }));
  await page.goto('./');
  await page.getByLabel('Choose drum recording').setInputFiles({ name: 'bad.txt', mimeType: 'text/plain', buffer: Buffer.from('not audio') });
  await expect(page.getByRole('alert')).toContainText('Choose a WAV');
  await page.getByRole('button', { name: 'Open Sunday pocket.wav' }).click();
  await page.getByRole('link', { name: 'Play & score' }).click();
  await expect(page.getByRole('heading', { name: 'The groove is ready.' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'MIDI', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'PDF', exact: true })).toBeDisabled();
});

test('mobile completed score renders, zooms, and restores after reload', async ({ page }, testInfo) => {
  await fixtures(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`./?page=play#job=${id}`);
  const score = page.getByRole('img', { name: 'Drum sheet music, page 1' });
  await expect(score).toBeVisible();
  await score.scrollIntoViewIfNeeded();
  await expect.poll(() => canvasColors(page, '.pdf-paper-area canvas')).toBeGreaterThan(3);
  const width = (await score.boundingBox())!.width;
  await page.getByRole('button', { name: 'Zoom score in' }).click();
  await expect.poll(async () => (await score.boundingBox())!.width).toBeGreaterThan(width);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('mobile-completed.png'), fullPage: true });
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Sunday pocket', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'MusicXML', exact: true })).toBeVisible();
});

for (const width of [320, 390, 768, 1440]) {
  test(`layout and top-down kit fit a ${width}px viewport`, async ({ page }, testInfo) => {
    await fixtures(page, []);
    await page.setViewportSize({ width, height: 900 });
    await page.goto('./');
    await expect(page.getByRole('button', { name: 'Add a recording' })).toBeVisible();
    await page.getByRole('link', { name: 'Play & score' }).click();
    const scene = page.locator('.top-kit-svg'); await scene.scrollIntoViewIfNeeded();
    await expect.poll(() => canvasColors(page, '.top-kit-svg')).toBeGreaterThan(40);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(await page.locator('button').evaluateAll(buttons => buttons.filter(button => button.clientWidth > 35 && button.scrollWidth > button.clientWidth + 2).map(button => button.textContent))).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`studio-${width}.png`), fullPage: true });
    await page.getByRole('button', { name: 'Crash cymbal' }).click();
    await expect(page.locator('.kit-instrument.is-hit')).toHaveCount(1);
  });
}