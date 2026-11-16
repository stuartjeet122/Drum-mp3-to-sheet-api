import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, ArrowRight, ArrowUpRight, AudioLines, Check, CheckCheck, ChevronRight, CircleAlert, Clock3, Drum, FileAudio, FileMusic, FileText, Headphones, LoaderCircle, Music2, Play, Plus, Radio, RefreshCw, ShieldCheck, Upload, X } from 'lucide-react';
import Player from './Player';
import { exportUrl, formatTime, isActive, request, stages, uploadAudio, validateAudio, type Job } from './api';
import { readMidi, type Sequence } from './music';

const PdfScore = lazy(() => import('./PdfScore'));

function savedJob(): string | null {
  try {
    const id = new URLSearchParams(location.hash.slice(1)).get('job') || localStorage.getItem('backbeat-job');
    return id && /^[0-9a-f-]{36}$/i.test(id) ? id : null;
  } catch { return null; }
}

function rememberJob(id: string) {
  try { localStorage.setItem('backbeat-job', id); } catch {}
  history.replaceState(null, '', `${location.pathname}${location.search}#job=${id}`);
}

export default function App() {
  const [job, setJob] = useState<Job | null>(null);
  const [recent, setRecent] = useState<Job[]>([]);
  const [online, setOnline] = useState<boolean | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadPercent, setUploadPercent] = useState(0);
  const [error, setError] = useState('');
  const [pollError, setPollError] = useState('');
  const [sequence, setSequence] = useState<Sequence | null>(null);
  const [midiLoading, setMidiLoading] = useState(false);
  const [midiError, setMidiError] = useState('');
  const [reloadMidi, setReloadMidi] = useState(0);
  const [opening, setOpening] = useState<string | null>(null);
  const [connectionCheck, setConnectionCheck] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const uploadController = useRef<AbortController | null>(null);
  const selectionController = useRef<AbortController | null>(null);
  const workbench = useRef<HTMLElement>(null);
  const active = job ? isActive(job) : false;
  const complete = job?.status === 'COMPLETED';
  const failed = job?.status === 'FAILED';
  const stageIndex = Math.max(0, stages.findIndex(stage => stage.codes.includes(job?.status || '')));
  const page = new URLSearchParams(location.search).get('page') === 'play' ? 'play' : 'upload';

  useEffect(() => {
    const controller = new AbortController();
    request<Job[]>('/transcriptions?limit=5', { signal: controller.signal }).then(jobs => {
      setRecent(jobs); setOnline(true);
    }).catch(error => { if (error.name !== 'AbortError') setOnline(false); });
    return () => controller.abort();
  }, [connectionCheck]);

  useEffect(() => {
    const id = savedJob();
    if (!id) return;
    const controller = new AbortController();
    request<Job>(`/transcriptions/${id}/status`, { signal: controller.signal }).then(setJob).catch(() => {});
    return () => controller.abort();
  }, []);

  useEffect(() => () => { uploadController.current?.abort(); selectionController.current?.abort(); }, []);

  useEffect(() => {
    if (!job) return;
    rememberJob(job.job_id);
    setRecent(previous => [job, ...previous.filter(item => item.job_id !== job.job_id)].slice(0, 5));
  }, [job]);

  useEffect(() => {
    if (!job || !isActive(job)) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    async function poll() {
      try {
        const next = await request<Job>(`/transcriptions/${job!.job_id}/status`, { signal: controller.signal });
        if (stopped) return;
        setJob(next); setPollError(''); setOnline(true);
        if (!isActive(next)) return;
      } catch (error) {
        if (stopped) return;
        setPollError(error instanceof Error ? error.message : 'Connection interrupted. Reconnecting...');
      }
      timer = setTimeout(poll, document.hidden ? 6000 : 2000);
    }
    timer = setTimeout(poll, 1200);
    return () => { stopped = true; clearTimeout(timer); controller.abort(); };
  }, [job?.job_id, active]);

  useEffect(() => {
    setSequence(null); setMidiError('');
    if (!job || job.status !== 'COMPLETED' || !job.available_exports.midi) { setMidiLoading(false); return; }
    const controller = new AbortController();
    let cancelled = false;
    setMidiLoading(true);
    fetch(exportUrl(job.job_id, 'midi'), { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('Your MIDI could not be loaded. Download it directly or try again.');
      return readMidi(await response.arrayBuffer());
    }).then(data => { if (!cancelled) setSequence(data); }).catch(error => {
      if (!cancelled) setMidiError(error instanceof Error ? error.message : 'Could not load MIDI playback.');
    }).finally(() => { if (!cancelled) setMidiLoading(false); });
    return () => { cancelled = true; controller.abort(); };
  }, [job?.job_id, complete, job?.updated_at, reloadMidi]);

  function choose(candidate: File | undefined) {
    if (!candidate || busy || active) return;
    const problem = validateAudio(candidate);
    if (problem) { setError(problem); return; }
    setError(''); setFile(candidate);
  }

  async function begin() {
    if (!file || busy || active) return;
    setBusy(true); setUploading(true); setUploadPercent(0); setError('');
    const controller = new AbortController();
    uploadController.current = controller;
    try {
      const uploaded = await uploadAudio(file, setUploadPercent, controller.signal);
      setJob(uploaded); setFile(null); setUploading(false);
      const started = await request<Job>(`/transcriptions/${uploaded.job_id}/start`, { method: 'POST', signal: controller.signal });
      setJob(started); setOnline(true);
    } catch (error) {
      if (error instanceof Error && error.name !== 'AbortError') setError(error.message);
    } finally { setBusy(false); setUploading(false); }
  }

  async function retry() {
    if (!job || busy) return;
    setBusy(true); setError('');
    try {
      const next = await request<Job>(`/transcriptions/${job.job_id}/start`, { method: 'POST' });
      setJob(next); setOnline(true);
    } catch (error) { setError(error instanceof Error ? error.message : 'Could not start transcription.'); }
    finally { setBusy(false); }
  }

  async function openJob(id: string) {
    if (busy) return;
    selectionController.current?.abort();
    const controller = new AbortController();
    selectionController.current = controller;
    setOpening(id); setError('');
    try {
      const next = await request<Job>(`/transcriptions/${id}/status`, { signal: controller.signal });
      setJob(next); setOnline(true); setFile(null);
    } catch (error) { if (error instanceof Error && error.name !== 'AbortError') setError(error.message); }
    finally { if (!controller.signal.aborted) setOpening(null); }
  }

  const date = new Intl.DateTimeFormat('en', { month: 'short', day: '2-digit' }).format(new Date());
  const jobFragment = job ? `#job=${job.job_id}` : '';
  const uploadUrl = `${import.meta.env.BASE_URL}?page=upload${jobFragment}`;
  const playUrl = `${import.meta.env.BASE_URL}?page=play${jobFragment}`;

  return <>
    <header className="site-header"><div className="header-inner"><a href={uploadUrl} className="wordmark" aria-label="Backbeat home"><span className="brand-mark"><Drum size={23} strokeWidth={1.7} /></span>backbeat<span className="brand-period">.</span><span className="brand-divider" /><span className="brand-descriptor">DRUM STUDIO</span></a><nav aria-label="Main navigation"><a href={uploadUrl} className={`nav-link ${page === 'upload' ? 'active' : ''}`}><span />Upload</a><a href={playUrl} className={`nav-link ${page === 'play' ? 'active' : ''}`}><span />Play & score</a></nav><div className="header-end"><span className={`connection ${online === false ? 'offline' : ''}`}><i />{online === null ? 'Connecting' : online ? 'Studio online' : 'Offline'}</span><a className="icon-button header-api" href="/drum-sheet-api/docs" target="_blank" rel="noopener noreferrer" title="Open API documentation" aria-label="Open API documentation"><ArrowUpRight size={18} /></a></div></div></header>
    <main id="workspace" ref={workbench} className={`${page}-page`}>
      <div className="workspace-intro"><div><div className="eyebrow"><span className="eyebrow-line" />{page === 'upload' ? 'A SPACE FOR YOUR RHYTHM' : 'YOUR SCORE, YOUR GROOVE'}</div><h1>{page === 'upload' ? <>Your drum studio<span>.</span></> : <>Play your score<span>.</span></>}</h1></div><div className="session-label"><span className="session-date">{date.toUpperCase()}</span><span className="session-rule" /><span>{page === 'upload' ? 'GOOD TAKES START HERE' : 'LISTEN · FOLLOW · PLAY'}</span></div></div>

      {page === 'upload' ? <div className="workspace-grid upload-page-grid">
        <div className="workflow-stack">
          <section className="source-section" aria-labelledby="source-title"><div className="section-heading"><div className="heading-with-icon"><span className="section-number">01</span><h2 id="source-title">Your recording</h2></div><span className="section-tag">DRUM STEM</span></div>
            <div className={`upload-zone ${dragging ? 'is-dragging' : ''} ${file ? 'has-file' : ''}`} onDragOver={event => { event.preventDefault(); if (!busy && !active) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); choose(event.dataTransfer.files[0]); }}>
              <input ref={input} type="file" accept=".wav,.mp3,.flac,.aiff,.aif" aria-label="Choose drum recording" className="sr-only" onChange={event => { choose(event.target.files?.[0]); event.target.value = ''; }} disabled={busy || active} />
              {file ? <><div className="selected-file-heading"><span className="upload-symbol"><FileAudio size={27} strokeWidth={1.4} /></span><button className="icon-button" title="Remove selected recording" aria-label="Remove selected recording" disabled={busy} onClick={() => setFile(null)}><X size={16} /></button></div><strong className="selected-file-name" title={file.name}>{file.name}</strong><span className="selected-file-size">{(file.size / (1024 * 1024)).toFixed(1)} MB <span /> READY FOR A TAKE</span><button className="primary-button transcribe-button" onClick={begin} disabled={busy || active}>{busy ? <LoaderCircle size={17} className="spin" /> : <AudioLines size={18} />}{uploading ? `Uploading ${uploadPercent}%` : busy ? 'Starting transcription...' : 'Create my score'}{!busy && <ArrowRight size={17} />}</button></> : <><div className="upload-art" aria-hidden="true"><div className="upload-art-lines"><span /><span /><span /><span /><span /><span /><span /><span /><span /></div><div className="upload-symbol"><Upload size={27} strokeWidth={1.5} /></div></div><h3>{active ? 'A groove in the making.' : 'A fresh take starts here.'}</h3><span className="upload-formats">WAV, MP3, FLAC, AIFF <i /> UP TO 100 MB</span><button className="primary-button" onClick={() => input.current?.click()} disabled={busy || active}><Plus size={18} />{active ? 'Transcription in progress' : 'Add a recording'}{!active && <ArrowUpRight size={16} />}</button></>}
              {uploading && <div className="upload-progress-bar" style={{ width: `${uploadPercent}%` }} />}
            </div>
            <div className="source-footnote"><ShieldCheck size={13} /><span>No account. Just your recording.</span><Headphones size={14} /></div>
            {error && <div className="error-banner" role="alert"><CircleAlert size={17} /><span>{error}</span><button className="icon-button" aria-label="Dismiss error" onClick={() => setError('')}><X size={15} /></button></div>}
          </section>

          {job && !file && <section className={`job-progress ${failed ? 'job-failed' : ''}`} aria-label="Transcription progress" aria-live="polite"><div className="progress-top"><span className={`status-mark ${complete ? 'done' : ''}`}>{complete ? <CheckCheck size={18} /> : failed ? <CircleAlert size={18} /> : job.status === 'UPLOADED' ? <FileAudio size={18} /> : <AudioLines size={18} className="soft-pulse" />}</span><div><h3>{complete ? 'Your groove, on paper.' : failed ? 'This take needs another try.' : job.status === 'UPLOADED' ? 'Recording uploaded.' : stages[stageIndex].title}</h3><p title={job.original_filename}>{job.original_filename}</p></div><strong>{job.progress_percent}<small>%</small></strong></div><div className="progress-track" role="progressbar" aria-label="Transcription completion" aria-valuenow={job.progress_percent} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${job.progress_percent}%` }} /></div>{!complete && !failed && job.status !== 'UPLOADED' && <ol className="stage-list">{stages.map((stage, index) => <li key={stage.title} className={index < stageIndex ? 'stage-complete' : index === stageIndex ? 'stage-current' : ''}><span>{index < stageIndex ? <Check size={10} /> : index + 1}</span>{stage.title}</li>)}</ol>}{complete && <><div className="result-metadata"><span>{job.bpm?.toFixed(1)} <small>BPM</small></span><i /><span>{job.time_signature} <small>TIME</small></span><i /><span>{formatTime(job.duration_sec || 0)} <small>LENGTH</small></span></div><a className="primary-button open-score-button" href={playUrl}><Play size={16} fill="currentColor" />Play & view score<ArrowRight size={16} /></a></>}{failed && <p className="failure-message">{job.error_message || 'The server could not finish this recording.'}</p>}{(failed || job.status === 'UPLOADED') && <button className="secondary-button retry-button" onClick={retry} disabled={busy}>{busy ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}{job.status === 'UPLOADED' ? 'Start transcription' : 'Retry transcription'}</button>}{pollError && <p className="connection-warning"><Radio size={13} />Connection interrupted. Reconnecting automatically.</p>}</section>}
        </div>

        <aside className="upload-aside">
          <section className="recordings-section" id="recordings" aria-labelledby="recordings-title"><div className="section-heading"><div className="heading-with-icon"><Clock3 size={15} /><h2 id="recordings-title">Recent recordings</h2></div><button className="icon-button" aria-label="Refresh recordings" title="Refresh recordings" onClick={() => setConnectionCheck(value => value + 1)}><RefreshCw size={14} /></button></div>{recent.length ? <div className="recordings-list">{recent.map(recording => <button key={recording.job_id} className={`recording-row ${job?.job_id === recording.job_id ? 'is-current' : ''}`} onClick={() => openJob(recording.job_id)} disabled={busy || opening !== null} aria-label={`Open ${recording.original_filename}`}><span className={`recording-icon ${recording.status === 'COMPLETED' ? 'ready' : recording.status === 'FAILED' ? 'failed' : ''}`}>{opening === recording.job_id || isActive(recording) ? <LoaderCircle size={18} className="spin" /> : recording.status === 'FAILED' ? <CircleAlert size={18} /> : <AudioLines size={18} />}</span><span className="recording-details"><strong title={recording.original_filename}>{recording.original_filename.replace(/\.[^.]+$/, '')}</strong><span>{recording.bpm ? `${Math.round(recording.bpm)} BPM` : recording.status === 'FAILED' ? 'Needs retry' : recording.status === 'UPLOADED' ? 'Uploaded' : 'Processing'}<i />{recording.duration_sec ? formatTime(recording.duration_sec) : recording.status === 'COMPLETED' ? 'Ready' : `${recording.progress_percent}%`}</span></span><span className={`recording-status ${recording.status === 'COMPLETED' ? 'ready' : ''}`}>{recording.status === 'COMPLETED' ? <Check size={13} /> : <ChevronRight size={14} />}</span></button>)}</div> : <div className="empty-library"><span className="empty-library-icon"><AudioLines size={21} strokeWidth={1.4} /></span><div><strong>{online === false ? 'The studio is reconnecting.' : 'A clean slate. A fresh rhythm.'}</strong><span>{online === false ? 'Your recordings will appear when connected.' : 'Your recordings belong here.'}</span></div></div>}</section>
          <div className="studio-note"><span className="studio-note-icon"><Drum size={24} strokeWidth={1.3} /></span><div><span>THE HUMAN PART STAYS.</span><p>Your timing. Your dynamics.<br />Your unmistakable groove.</p></div><span className="note-asterisk" aria-hidden="true">*</span></div>
        </aside>
      </div> : <>
        <div className="play-page-intro"><p>{job?.original_filename ? `Now playing: ${job.original_filename}` : 'Explore the built-in groove or open a completed recording from your library.'}</p><a className="secondary-button" href={uploadUrl}><Upload size={15} />Upload a recording</a></div>
        <div className="play-page-player"><Player sequence={sequence} title={job?.original_filename || ''} loading={midiLoading} jobId={job?.job_id ?? null} voiceFilename={job?.voice_filename ?? null} onVoiceFilenameChange={filename => { if (job) setJob(previous => previous && previous.job_id === job.job_id ? { ...previous, voice_filename: filename } : previous); }} />{midiError && <div className="error-banner" role="alert"><CircleAlert size={16} /><span>{midiError}</span><button className="icon-button" onClick={() => setReloadMidi(value => value + 1)} aria-label="Retry MIDI loading" title="Retry MIDI loading"><RefreshCw size={15} /></button></div>}</div>
        <section className="score-section" aria-labelledby="score-title" id="score"><div className="score-section-heading"><div><div className="eyebrow"><span className="section-number">02</span>THE FINAL TAKE</div><h2 id="score-title">On the record<span>.</span></h2></div><div className="export-actions">{([{ id: 'pdf', label: 'PDF', icon: FileText }, { id: 'midi', label: 'MIDI', icon: FileMusic }, { id: 'musicxml', label: 'MusicXML', icon: Music2 }] as const).map(format => complete && job.available_exports[format.id] ? <a key={format.id} className={`export-button ${format.id === 'pdf' ? 'export-primary' : ''}`} href={exportUrl(job.job_id, format.id)} download><format.icon size={16} />{format.label}<ArrowDownToLine size={14} /></a> : <button key={format.id} className="export-button" disabled title={complete ? `${format.label} is unavailable for this recording` : 'Available when your score is ready'}><format.icon size={16} />{format.label}<ArrowDownToLine size={14} /></button>)}</div></div>
          {complete && job.available_exports.pdf ? <Suspense fallback={<div className="score-loading"><LoaderCircle className="spin" size={24} /></div>}><PdfScore jobId={job.job_id} /></Suspense> : <div className="score-empty"><div className="empty-score-paper" aria-hidden="true"><div className="paper-title-line" /><div className="paper-subtitle-line" /><div className="empty-staff"><i /><i /><i /><i /><i /></div><div className="empty-staff"><i /><i /><i /><i /><i /></div><Music2 size={29} strokeWidth={1.2} /></div><div className="score-empty-copy"><span className="section-tag">{complete ? 'PDF UNAVAILABLE' : active ? 'IN THE MAKING' : 'THE NEXT CHAPTER'}</span><h3>{complete ? 'The groove is ready.' : active ? 'A score worth waiting for.' : 'Good grooves deserve paper.'}</h3><p>{complete ? 'Your MIDI and MusicXML are ready above.' : active ? stages[stageIndex].title : 'Open a completed recording to view its score.'}</p></div><span className="score-empty-number" aria-hidden="true">02</span></div>}
        </section>
      </>}
      <footer className="site-footer"><a href={uploadUrl} className="footer-brand"><Drum size={16} />backbeat.</a><span>MADE FOR THE POCKET.</span><span className="footer-right">KEEP YOUR RHYTHM HUMAN <span className="footer-spark">+</span></span></footer>
    </main>
  </>;
}