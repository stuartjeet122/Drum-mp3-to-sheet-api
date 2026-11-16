import { afterEach, describe, expect, it, vi } from 'vitest';
import { deleteVoiceStem, downloadVoiceStem, exportUrl, formatTime, isActive, request, updateNotation, uploadedJob, uploadVoiceStem, validateAudio, voiceStemUrl, type Job } from './api';

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
  expect(voiceStemUrl('abc')).toBe('/drum-sheet-api/api/transcriptions/abc/voice');
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
  expect(result.voice_filename).toBeNull();
  expect(isActive(result)).toBe(false);
});

it('downloads a saved vocal and removes it through the job-scoped API', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(new Blob(['ID3 vocal']), { status: 200, headers: { 'Content-Type': 'audio/mpeg' } }))
    .mockResolvedValueOnce(new Response(null, { status: 204 }));
  vi.stubGlobal('fetch', fetchMock);
  const file = await downloadVoiceStem('job-id', 'voice.mp3', new AbortController().signal);
  expect(file.name).toBe('voice.mp3');
  expect(file.type).toBe('audio/mpeg');
  await expect(deleteVoiceStem('job-id')).resolves.toBeUndefined();
  expect(fetchMock.mock.calls.map(([url, options]) => [url, options?.method])).toEqual([
    ['/drum-sheet-api/api/transcriptions/job-id/voice', undefined],
    ['/drum-sheet-api/api/transcriptions/job-id/voice', 'DELETE'],
  ]);
});

it('surfaces errors from vocal downloads and deletions', async () => {
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'Vocal not found' }), { status: 404 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'Storage unavailable' }), { status: 500 })));
  await expect(downloadVoiceStem('job-id', 'voice.mp3', new AbortController().signal)).rejects.toThrow('Vocal not found');
  await expect(deleteVoiceStem('job-id')).rejects.toThrow('Storage unavailable');
});

it('sends validated notation settings to the cached score rebuild endpoint', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'NOTATION_GENERATION' })));
  vi.stubGlobal('fetch', fetchMock);
  const options = {
    show_dynamics: true, show_accents: true, show_sticking: true,
    accent_threshold: 105, measures_per_system: 3,
    sections: [{ start_measure: 1, end_measure: 4, label: 'Intro', repeat: true }],
  };
  await updateNotation('job/id', options);
  expect(fetchMock).toHaveBeenCalledWith('/drum-sheet-api/api/transcriptions/job%2Fid/notation', {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(options),
  });
});

it('uploads a vocal stem with progress and surfaces server errors', async () => {
  class MockXHR {
    upload = { onprogress: null as ((event: ProgressEvent) => void) | null };
    responseType = '';
    timeout = 0;
    status = 200;
    response: unknown = { job_id: 'job-id', voice_filename: 'voice.mp3', file_size_bytes: 12 };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    ontimeout: (() => void) | null = null;
    onabort: (() => void) | null = null;
    onloadend: (() => void) | null = null;
    open = vi.fn();
    send = vi.fn(() => this.onload?.());
    abort = vi.fn(() => this.onabort?.());
  }
  const xhr = new MockXHR();
  vi.stubGlobal('XMLHttpRequest', vi.fn(() => xhr));
  const progress = vi.fn();
  await expect(uploadVoiceStem('job-id', new File(['ID3 audio'], 'voice.mp3'), progress, new AbortController().signal))
    .resolves.toEqual({ job_id: 'job-id', voice_filename: 'voice.mp3', file_size_bytes: 12 });
  expect(xhr.open).toHaveBeenCalledWith('POST', '/drum-sheet-api/api/transcriptions/job-id/voice');

  xhr.status = 415;
  xhr.response = { detail: 'Voice stems must be MP3 files.' };
  await expect(uploadVoiceStem('job-id', new File(['audio'], 'voice.wav'), progress, new AbortController().signal))
    .rejects.toThrow('Voice stems must be MP3 files.');
});