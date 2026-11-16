import { AlertTriangle, LoaderCircle, RefreshCw } from 'lucide-react';
import { formatTime, stages, type Job } from './api';

function stageName(code: string | null): string {
  return stages.find(stage => stage.codes.includes(code || ''))?.title
    || (code ? code.toLowerCase().replace(/_/g, ' ') : 'Unknown stage');
}

function advice(job: Job): string {
  switch (job.failed_stage) {
    case 'PREPROCESSING':
      return 'Try a shorter WAV or MP3 exported at a standard sample rate, and confirm the file plays normally.';
    case 'QUALITY_ANALYSIS':
      return 'Use a clean drum stem with a strong signal and less background noise.';
    case 'ADTOF_TRANSCRIPTION':
    case 'OAF_VERIFICATION':
      return 'Check that the transcription models are installed and the worker is healthy, then retry. This action reruns transcription.';
    case 'EVENT_REFINEMENT':
    case 'RHYTHM_ANALYSIS':
      return 'Try a clear drum-only stem with a steady audible pulse. Retrying will rerun the analysis stages.';
    case 'NOTATION_GENERATION':
      return job.retry_stage === 'NOTATION_GENERATION'
        ? 'The prepared audio and rhythm cache are available, so retry will rebuild notation without rerunning transcription.'
        : 'Cached notation data are unavailable; retry will need to rerun the full transcription.';
    default:
      return 'Check the worker and model availability, then retry the failed recording.';
  }
}

export default function JobDiagnostics({ job, busy, onRetry }: {
  job: Job;
  busy: boolean;
  onRetry: () => void;
}) {
  const warnings = job.quality_metrics?.warnings || [];
  if (job.status !== 'FAILED' && warnings.length === 0 && job.estimated_wait_seconds == null) return null;

  return <section className={`job-diagnostics ${job.status === 'FAILED' ? 'has-failure' : ''}`} aria-label="Job diagnostics" aria-live="polite">
    {job.status === 'FAILED' ? <>
      <div className="diagnostic-heading"><AlertTriangle size={16} /><strong>Processing needs attention</strong></div>
      <p className="diagnostic-stage">Failed during {stageName(job.failed_stage)}.</p>
      <p>{job.error_message || 'The server could not finish this recording.'}</p>
      <p>{advice(job)}</p>
      {job.retry_stage && <button className="secondary-button retry-button" onClick={onRetry} disabled={busy}>
        {busy ? <LoaderCircle size={14} className="spin" /> : <RefreshCw size={14} />}
        {job.retry_stage === 'NOTATION_GENERATION' ? 'Retry score generation' : 'Retry transcription'}
      </button>}
    </> : <>
      {job.estimated_wait_seconds != null && <p><LoaderCircle size={14} className="spin" />Approximate time remaining: {formatTime(job.estimated_wait_seconds)}. Estimate is based on recent jobs.</p>}
      {warnings.length > 0 && <div className="diagnostic-warnings"><strong>Audio checks</strong><ul>{warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul></div>}
    </>}
  </section>;
}
