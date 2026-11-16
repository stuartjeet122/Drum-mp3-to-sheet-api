import { afterEach, describe, expect, it, vi } from 'vitest';
import { exportUrl, formatTime, isActive, request, uploadedJob, validateAudio, type Job } from './api';

afterEach(() => vi.unstubAllGlobals());

describe('recording validation', () => {
  it('accepts supported recordings and rejects empty, oversized and unsupported files', () => {
    expect(validateAudio({ name: 'take.MP3', size: 1024 })).toBeNull();
    expect(validateAudio({ name: 'take.wav', size: 0 })).toContain('empty');
    expect(validateAudio({ name: 'take.flac', size: 101 * 1024 * 1024 })).toContain('100 MB');
    expect(validateAudio({ name: 'take.exe', size: 1024 })).toContain('Choose');
  });
});

it('formats playback time defensively', () => {
  expect(formatTime(177.449)).toBe('2:57');
  expect(formatTime(-1)).toBe('0:00');
  expect(formatTime(Number.NaN)).toBe('0:00');
});

it('only polls active jobs', () => {
  expect(isActive({ status: 'OAF_VERIFICATION' } as Job)).toBe(true);
  for (const status of ['FAILED', 'COMPLETED', 'UPLOADED']) expect(isActive({ status } as Job)).toBe(false);
});

it('uses the deployed export contract', () => {
  expect(exportUrl('abc', 'musicxml')).toBe('/drum-sheet-api/api/transcriptions/abc/export/musicxml');
});

it('surfaces server errors instead of treating them as successful jobs', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Queue unavailable' }), { status: 503 })));
  await expect(request('/transcriptions/id/start', { method: 'POST' })).rejects.toThrow('Queue unavailable');
});

it('reads committed status responses', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'COMPLETED' }))));
  await expect(request('/transcriptions/id/status')).resolves.toEqual({ status: 'COMPLETED' });
});

it('normalizes the smaller upload response without inventing ready exports', () => {
  const result = uploadedJob({ job_id: 'id', status: 'UPLOADED', original_filename: 'take.mp3', created_at: '2026-11-16' });
  expect(result.available_exports).toEqual({ midi: false, musicxml: false, pdf: false });
  expect(result.progress_percent).toBe(0);
  expect(isActive(result)).toBe(false);
});