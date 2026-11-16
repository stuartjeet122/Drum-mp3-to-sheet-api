export interface Job {
  job_id: string;
  status: string;
  progress_percent: number;
  original_filename: string;
  voice_filename: string | null;
  bpm: number | null;
  time_signature: string;
  duration_sec: number | null;
  error_message: string | null;
  available_exports: { midi: boolean; musicxml: boolean; pdf: boolean };
  updated_at: string;
}

export const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/drum-sheet-api').replace(/\/$/, '');
export const exportUrl = (id: string, format: string) => `${API_BASE}/api/transcriptions/${encodeURIComponent(id)}/export/${format}`;
export const voiceStemUrl = (id: string) => `${API_BASE}/api/transcriptions/${encodeURIComponent(id)}/voice`;
export const isActive = (job: Job) => !['COMPLETED', 'FAILED', 'UPLOADED'].includes(job.status);

export function uploadedJob(value: { job_id: string; status: string; original_filename: string; created_at: string }): Job {
  return {
    job_id: value.job_id, status: value.status, original_filename: value.original_filename,
    updated_at: value.created_at, progress_percent: 0, bpm: null, duration_sec: null,
    time_signature: '4/4', error_message: null, voice_filename: null,
    available_exports: { midi: false, musicxml: false, pdf: false },
  };
}

export const stages = [
  { title: 'Preparing audio', codes: ['PREPROCESSING', 'QUALITY_ANALYSIS'] },
  { title: 'Listening for drums', codes: ['ADTOF_TRANSCRIPTION', 'OAF_VERIFICATION', 'EVENT_REFINEMENT'] },
  { title: 'Finding the groove', codes: ['RHYTHM_ANALYSIS'] },
  { title: 'Writing your score', codes: ['NOTATION_GENERATION', 'COMPLETED'] },
];

export function formatTime(seconds: number): string {
  const safe = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, '0')}`;
}

export function validateAudio(file: Pick<File, 'name' | 'size'>): string | null {
  if (!/\.(wav|mp3|flac|aiff|aif)$/i.test(file.name)) return 'Choose a WAV, MP3, FLAC, or AIFF recording.';
  if (file.size === 0) return 'This file is empty. Choose another recording.';
  if (file.size > 100 * 1024 * 1024) return 'This recording is over the 100 MB limit.';
  return null;
}

function errorText(body: unknown, fallback: string): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map(item => item.msg || 'Invalid request').join('. ');
  }
  return fallback;
}

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}/api${path}`, options);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(errorText(body, `The server returned ${response.status}. Please try again.`));
  }
  return response.json() as Promise<T>;
}

export function uploadAudio(file: File, progress: (value: number) => void, signal: AbortSignal): Promise<Job> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    signal.addEventListener('abort', abort, { once: true });
    xhr.open('POST', `${API_BASE}/api/transcriptions`);
    xhr.responseType = 'json';
    xhr.timeout = 300_000;
    xhr.upload.onprogress = event => {
      if (event.lengthComputable) progress(Math.round(event.loaded / event.total * 100));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(uploadedJob(xhr.response));
      else reject(new Error(errorText(xhr.response, `Upload failed (${xhr.status}). Please try again.`)));
    };
    xhr.onerror = () => reject(new Error('Could not reach the server. Check your connection and try again.'));
    xhr.ontimeout = () => reject(new Error('The upload timed out. Please try again.'));
    xhr.onabort = () => reject(new DOMException('Upload cancelled', 'AbortError'));
    xhr.onloadend = () => signal.removeEventListener('abort', abort);
    const form = new FormData();
    form.append('file', file);
    if (signal.aborted) reject(new DOMException('Upload cancelled', 'AbortError'));
    else xhr.send(form);
  });
}

export interface VoiceStemUpload {
  job_id: string;
  voice_filename: string;
  file_size_bytes: number;
}

export function uploadVoiceStem(
  jobId: string, file: File, progress: (value: number) => void, signal: AbortSignal,
): Promise<VoiceStemUpload> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    signal.addEventListener('abort', abort, { once: true });
    xhr.open('POST', voiceStemUrl(jobId));
    xhr.responseType = 'json';
    xhr.timeout = 300_000;
    xhr.upload.onprogress = event => {
      if (event.lengthComputable) progress(Math.round(event.loaded / event.total * 100));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.response as VoiceStemUpload);
      else reject(new Error(errorText(xhr.response, `Vocal upload failed (${xhr.status}). Please try again.`)));
    };
    xhr.onerror = () => reject(new Error('Could not reach the server. Check your connection and try again.'));
    xhr.ontimeout = () => reject(new Error('The vocal upload timed out. Please try again.'));
    xhr.onabort = () => reject(new DOMException('Upload cancelled', 'AbortError'));
    xhr.onloadend = () => signal.removeEventListener('abort', abort);
    const form = new FormData();
    form.append('file', file);
    if (signal.aborted) reject(new DOMException('Upload cancelled', 'AbortError'));
    else xhr.send(form);
  });
}

export async function downloadVoiceStem(jobId: string, filename: string, signal: AbortSignal): Promise<File> {
  const response = await fetch(voiceStemUrl(jobId), { signal });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(errorText(body, `The saved vocal could not be loaded (${response.status}).`));
  }
  const blob = await response.blob();
  return new File([blob], filename, { type: blob.type || 'audio/mpeg' });
}

export async function deleteVoiceStem(jobId: string): Promise<void> {
  const response = await fetch(voiceStemUrl(jobId), { method: 'DELETE' });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(errorText(body, `The vocal could not be removed (${response.status}).`));
  }
}