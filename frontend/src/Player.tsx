import { lazy, Suspense, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { Activity, CirclePause, FileAudio, Headphones, LoaderCircle, Pause, Play, Repeat2, SkipBack, Upload, Volume2, VolumeX, X } from 'lucide-react';
import type { DrumKitHandle } from './DrumKit';
import { demoGroove, drumForNote, drums, humanizeHits, type Drum, type HumanizeSettings, type Sequence } from './music';
import { deleteVoiceStem, downloadVoiceStem, formatTime, uploadVoiceStem } from './api';
import type { DrumAudio } from './audio';

const Kit = lazy(() => import('./DrumKit').then(module => ({ default: module.DrumKit })));
const demo = demoGroove();

function DrumPads({ active, onHit, visible }: { active: Drum | null; onHit: (drum: Drum) => void; visible: typeof drums }) {
  return <div className="drum-pads" style={{ '--pad-count': visible.length } as CSSProperties}>{visible.map(drum => <button key={drum.id} className={`drum-pad ${active === drum.id ? 'is-hit' : ''}`} onClick={() => onHit(drum.id)} title={`Play ${drum.name}`} style={{ '--pad-color': drum.color } as CSSProperties}><span className="pad-dot" />{drum.name}</button>)}</div>;
}

export default function Player({
  sequence, title, loading, jobId, voiceFilename, onVoiceFilenameChange,
  timeSignature = '4/4',
}: {
  sequence: Sequence | null;
  title: string;
  loading: boolean;
  jobId: string | null;
  voiceFilename: string | null;
  onVoiceFilenameChange: (filename: string | null) => void;
  timeSignature?: string;
}) {
  const music = sequence || demo;
  const [swing, setSwing] = useState(0);
  const [timingMs, setTimingMs] = useState(0);
  const [velocityPercent, setVelocityPercent] = useState(0);
  const [quantizePercent, setQuantizePercent] = useState(0);
  const humanizeSettings: HumanizeSettings = { swing, timingMs, velocityPercent, quantizePercent };
  const playbackHits = useMemo(() => humanizeHits(music.hits, music.bpm, music.duration, humanizeSettings),
    [music, swing, timingMs, velocityPercent, quantizePercent]);
  const [drumVolume, setDrumVolume] = useState(100);
  const [voiceVolume, setVoiceVolume] = useState(63);
  const [masterVolume, setMasterVolume] = useState(50);
  const [audioLoadState, setAudioLoadState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [practiceLoop, setPracticeLoop] = useState(false);
  const [sectionStart, setSectionStart] = useState(1);
  const [sectionEnd, setSectionEnd] = useState(1);
  const [countIn, setCountIn] = useState(true);
  const [tempoRamp, setTempoRamp] = useState(0);
  const [sectionProgress, setSectionProgress] = useState(0);
  const [practiceRate, setPracticeRate] = useState(1);
  const [meterNumerator, meterDenominator] = timeSignature.split('/').map(value => Number(value) || 4);
  const measureDuration = 60 / music.bpm * meterNumerator * 4 / meterDenominator;
  const measureCount = Math.max(1, Math.ceil(music.duration / measureDuration));
  const visibleDrums = useMemo(() => {
    if (!sequence) return drums;
    const detected = new Set<Drum>();
    for (const hit of music.hits) {
      const drum = drumForNote(hit.midi);
      if (drum) detected.add(drum);
    }
    return drums.filter(drum => detected.has(drum.id));
  }, [music, sequence]);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [rate, setRate] = useState(1);
  const [muted, setMuted] = useState(false);
  const [loop, setLoop] = useState(false);
  const [active, setActive] = useState<Drum | null>(null);
  const [error, setError] = useState('');
  const [unlocking, setUnlocking] = useState(false);
  const [voiceFile, setVoiceFile] = useState<File | null>(null);
  const [voiceUrl, setVoiceUrl] = useState<string | null>(null);
  const [voiceDuration, setVoiceDuration] = useState<number | null>(null);
  const [voiceLoading, setVoiceLoading] = useState(false);
  const [voiceReady, setVoiceReady] = useState(false);
  const [voiceSaving, setVoiceSaving] = useState(false);
  const [voicePersisted, setVoicePersisted] = useState(false);
  const [voiceUploadPercent, setVoiceUploadPercent] = useState(0);
  const [voiceError, setVoiceError] = useState('');
  const kit = useRef<DrumKitHandle>(null);
  const audio = useRef<DrumAudio | null>(null);
  const position = useRef(0);
  const pulseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const voiceInput = useRef<HTMLInputElement>(null);
  const loopRef = useRef(loop);
  const volumeRef = useRef(muted);
  const audioPromise = useRef<Promise<DrumAudio> | null>(null);
  const voiceChange = useRef(0);
  const voiceRequest = useRef<AbortController | null>(null);
  useEffect(() => { loopRef.current = loop; }, [loop]);
  useEffect(() => { setPracticeRate(rate); }, [rate]);
  useEffect(() => {
    volumeRef.current = muted;
    audio.current?.mute(muted);
  }, [muted]);
  useEffect(() => {
    audio.current?.drumLevel(drumVolume);
    audio.current?.voiceLevel(voiceVolume);
    audio.current?.masterLevel(masterVolume);
  }, [drumVolume, voiceVolume, masterVolume]);
  const sectionProgressKey = `backbeat-practice-${jobId || 'demo'}-${sectionStart}-${sectionEnd}`;
  useEffect(() => {
    try { setSectionProgress(Number(localStorage.getItem(sectionProgressKey)) || 0); }
    catch { setSectionProgress(0); }
  }, [sectionProgressKey]);
  useEffect(() => () => {
    if (pulseTimer.current) clearTimeout(pulseTimer.current);
    voiceRequest.current?.abort();
    audio.current?.stop();
    void audio.current?.loadVoice(null);
    audio.current?.dispose();
    audio.current = null;
    audioPromise.current = null;
  }, []);
  useEffect(() => {
    if (!voiceFile) { setVoiceUrl(null); return; }
    const url = URL.createObjectURL(voiceFile);
    setVoiceUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [voiceFile]);

  useEffect(() => {
    const version = ++voiceChange.current;
    voiceRequest.current?.abort();
    setVoiceFile(null);
    setVoicePersisted(false);
    setVoiceSaving(false);
    setVoiceLoading(false);
    setVoiceUploadPercent(0);
    setVoiceError('');
    if (!jobId || !voiceFilename) return;

    const controller = new AbortController();
    voiceRequest.current = controller;
    setVoiceLoading(true);
    void downloadVoiceStem(jobId, voiceFilename, controller.signal).then(file => {
      if (version !== voiceChange.current) return;
      setVoiceFile(file);
      setVoicePersisted(true);
    }).catch(error => {
      if (version !== voiceChange.current || error instanceof Error && error.name === 'AbortError') return;
      setVoiceError(error instanceof Error ? error.message : 'Could not restore the saved vocal stem.');
    }).finally(() => {
      if (version === voiceChange.current) setVoiceLoading(false);
    });
    return () => controller.abort();
  }, [jobId, voiceFilename]);

  async function getEngine() {
    audioPromise.current ||= import('./audio').then(async ({ DrumAudio }) => {
      const engine = new DrumAudio();
      audio.current = engine;
      return engine;
    });
    return audioPromise.current;
  }

  async function getAudio() {
    const engine = await getEngine();
    if (audioLoadState === 'ready') {
      engine.mute(volumeRef.current);
      return engine;
    }
    setAudioLoadState('loading');
    try {
      await engine.unlock();
      engine.mute(volumeRef.current);
      engine.drumLevel(drumVolume);
      engine.voiceLevel(voiceVolume);
      engine.masterLevel(masterVolume);
      setAudioLoadState('ready');
      return engine;
    } catch (error) {
      audio.current?.dispose();
      audio.current = null;
      audioPromise.current = null;
      setAudioLoadState('error');
      throw error;
    }
  }

  useEffect(() => {
    let cancelled = false;
    setVoiceError('');
    if (!voiceUrl) {
      setVoiceLoading(false);
      setVoiceReady(false);
      setVoiceDuration(null);
      if (audio.current) void audio.current.loadVoice(null);
      return;
    }

    setVoiceLoading(true);
    setVoiceReady(false);
    getEngine().then(engine => engine.loadVoice(voiceUrl)).then(duration => {
      if (cancelled) return;
      if (duration === null) throw new Error('The voice stem could not be decoded.');
      setVoiceDuration(duration);
      setVoiceReady(true);
    }).catch(() => {
      if (!cancelled) setVoiceError('This MP3 voice stem could not be loaded. Choose another file.');
    }).finally(() => {
      if (!cancelled) setVoiceLoading(false);
    });
    return () => { cancelled = true; };
  }, [voiceUrl]);

  function pulse(drum: Drum, velocity = 1) {
    kit.current?.hit(drum, velocity);
    setActive(drum);
    if (pulseTimer.current) clearTimeout(pulseTimer.current);
    pulseTimer.current = setTimeout(() => setActive(null), 220);
  }

  async function strike(drum: Drum) {
    pulse(drum);
    try {
      const engine = await getAudio();
      const note = drums.find(item => item.id === drum)!.note;
      engine.hit({ time: 0, duration: 0.1, midi: note, velocity: 0.8 });
    } catch { setError('Drum samples could not load or audio could not start. Check your connection and browser audio permissions.'); }
  }

  function chooseVoice(file: File | undefined) {
    if (!file) return;
    if (!/\.mp3$/i.test(file.name)) { setVoiceError('Choose an MP3 voice stem.'); return; }
    if (file.size === 0) { setVoiceError('This MP3 is empty. Choose another file.'); return; }
    if (file.size > 100 * 1024 * 1024) { setVoiceError('The voice stem is over the 100 MB limit.'); return; }
    setVoiceError('');
    setPlaying(false);
    setTime(0);
    position.current = 0;
    const version = ++voiceChange.current;
    voiceRequest.current?.abort();
    setVoiceFile(file);
    setVoicePersisted(false);
    setVoiceUploadPercent(0);
    if (!jobId) {
      setVoiceSaving(false);
      return;
    }

    const controller = new AbortController();
    voiceRequest.current = controller;
    setVoiceSaving(true);
    void uploadVoiceStem(jobId, file, value => {
      if (version === voiceChange.current) setVoiceUploadPercent(value);
    }, controller.signal).then(result => {
      if (version !== voiceChange.current) return;
      setVoicePersisted(true);
      onVoiceFilenameChange(result.voice_filename);
    }).catch(error => {
      if (version !== voiceChange.current || error instanceof Error && error.name === 'AbortError') return;
      setVoiceError(error instanceof Error
        ? `This vocal is playing for now, but could not be saved to the server: ${error.message}`
        : 'This vocal is playing for now, but could not be saved to the server.');
    }).finally(() => {
      if (version === voiceChange.current) setVoiceSaving(false);
    });
  }

  async function removeVoice() {
    const version = ++voiceChange.current;
    voiceRequest.current?.abort();
    setVoiceSaving(true);
    setVoiceError('');
    try {
      if (jobId && voiceFilename) await deleteVoiceStem(jobId);
      if (version !== voiceChange.current) return;
      setVoiceFile(null);
      setVoicePersisted(false);
      onVoiceFilenameChange(null);
    } catch (error) {
      if (version !== voiceChange.current) return;
      setVoiceError(error instanceof Error ? error.message : 'Could not remove the saved vocal stem. Try again.');
    } finally {
      if (version === voiceChange.current) setVoiceSaving(false);
    }
  }

  useEffect(() => {
    setPlaying(false); setTime(0); position.current = 0;
    audio.current?.stop();
  }, [music]);

  useEffect(() => {
    if (!playing || !audio.current) return;
    const engine = audio.current;
    let currentRate = rate;
    let startPosition = practiceLoop ? Math.min((sectionStart - 1) * measureDuration, music.duration) : position.current;
    const playbackEnd = practiceLoop ? Math.min(sectionEnd * measureDuration, music.duration) : music.duration;
    let startTime = engine.now();
    if (practiceLoop && countIn) {
      const countBeat = 60 / music.bpm * 4 / meterDenominator / currentRate;
      const countBeats = meterNumerator;
      const countStart = startTime;
      for (let beat = 0; beat < countBeats; beat++) engine.countIn(countStart + beat * countBeat, beat === 0);
      startTime += countBeats * countBeat;
    }
    engine.voiceRate(currentRate);
    if (voiceUrl && voiceReady) engine.playVoice(startTime, startPosition);
    const firstHitIndex = playbackHits.findIndex(hit => hit.time >= startPosition - 0.001);
    const sectionFirstHitIndex = firstHitIndex < 0 ? playbackHits.length : firstHitIndex;
    let index = sectionFirstHitIndex;
    const visual: { at: number; drum: Drum; velocity: number }[] = [];
    let lastPaint = 0;
    const timer = window.setInterval(() => {
      const now = engine.now();
      const current = Math.max(startPosition, startPosition + (now - startTime) * currentRate);
      if (current >= playbackEnd) {
        if (practiceLoop) {
          setSectionProgress(previous => {
            const next = previous + 1;
            try { localStorage.setItem(sectionProgressKey, String(next)); } catch {}
            return next;
          });
          let nextStartTime = now + 0.01;
          if (countIn) {
            const countBeat = 60 / music.bpm * 4 / meterDenominator / currentRate;
            const countBeats = meterNumerator;
            for (let beat = 0; beat < countBeats; beat++) engine.countIn(nextStartTime + beat * countBeat, beat === 0);
            nextStartTime += countBeats * countBeat;
          }
          currentRate = Math.min(1.5, currentRate + tempoRamp / 100);
          setPracticeRate(currentRate);
          engine.voiceRate(currentRate);
          if (voiceUrl && voiceReady) engine.restartVoice(nextStartTime, startPosition);
          startTime = nextStartTime; index = sectionFirstHitIndex; visual.length = 0;
          position.current = startPosition; setTime(startPosition);
        } else if (loopRef.current) {
          engine.restartVoice(now, 0);
          startTime = now + 0.01; startPosition = 0; index = 0; visual.length = 0;
          position.current = 0; setTime(0);
        } else {
          position.current = 0; setTime(0); setPlaying(false); return;
        }
      } else {
        position.current = current;
        if (performance.now() - lastPaint > 60) { setTime(current); lastPaint = performance.now(); }
      }
      const elapsed = Math.max(startPosition, startPosition + (now - startTime) * currentRate);
      try {
        while (index < playbackHits.length && playbackHits[index].time < playbackEnd && playbackHits[index].time <= elapsed + 0.06 * currentRate) {
          const hit = playbackHits[index++];
          const drum = drumForNote(hit.midi);
          if (!drum) continue;
          const at = Math.max(now, startTime + (hit.time - startPosition) / currentRate);
          engine.hit(hit, at);
          visual.push({ at, drum, velocity: hit.velocity });
        }
        while (visual.length && visual[0].at <= now) {
          const hit = visual.shift()!;
          pulse(hit.drum, hit.velocity);
        }
      } catch {
        setPlaying(false); setError('Playback was interrupted. Press play to try again.');
      }
    }, 20);
    return () => { clearInterval(timer); engine.stop(); };
  }, [playing, rate, music, playbackHits, voiceUrl, voiceReady, practiceLoop, sectionStart,
    sectionEnd, measureDuration, countIn, tempoRamp, sectionProgressKey, meterDenominator, meterNumerator]);

  async function toggle() {
    if (playing) { setPlaying(false); return; }
    setUnlocking(true); setError('');
    try { await getAudio(); setPlaying(true); }
    catch { setError('Drum samples could not load or audio could not start. Check your connection and browser audio permissions.'); }
    finally { setUnlocking(false); }
  }

  function seek(value: number) {
    if (practiceLoop) setPracticeLoop(false);
    setPlaying(false); position.current = value; setTime(value);
  }

  function retryAudio() {
    setError('');
    setAudioLoadState('idle');
    void toggle();
  }

  async function togglePractice() {
    if (practiceLoop) {
      setPracticeLoop(false);
      return;
    }
    setLoop(false);
    setPracticeLoop(true);
    const start = Math.min((sectionStart - 1) * measureDuration, music.duration);
    position.current = start;
    setTime(start);
    setUnlocking(true);
    setError('');
    try {
      await getAudio();
      setPlaying(true);
    } catch {
      setPracticeLoop(false);
      setError('Drum samples could not load or audio could not start. Check your connection and browser audio permissions.');
    } finally {
      setUnlocking(false);
    }
  }

  const bars = useMemo(() => {
    const bins = Array<number>(92).fill(0);
    for (const hit of playbackHits) {
      const bin = Math.min(91, Math.floor(hit.time / music.duration * 92));
      bins[bin] += hit.velocity * (drumForNote(hit.midi) === 'kick' ? 1.8 : 1);
    }
    return bins.map(value => Math.max(0.08, Math.min(1, value / 2.8)));
  }, [music, playbackHits]);
  const volumeControls = [
    { label: 'Drum volume', value: drumVolume, onChange: setDrumVolume },
    { label: 'Vocal volume', value: voiceVolume, onChange: setVoiceVolume },
    { label: 'Master volume', value: masterVolume, onChange: setMasterVolume },
  ];

  return <section className="player-section" aria-labelledby="kit-heading">
    <div className="section-heading player-heading"><div className="heading-with-icon"><span className="tiny-cross" aria-hidden="true">+</span><h2 id="kit-heading">The live room</h2></div><span className={`live-badge ${playing ? 'playing' : ''}`}><i />{playing ? 'PLAYING LIVE' : 'READY TO PLAY'}</span></div>
    <div className="kit-stage">
      <div className="kit-stage-meta"><span>{sequence ? `DETECTED / ${visibleDrums.length}-PIECE` : `DEMO / ${visibleDrums.length}-PIECE`}</span><Headphones size={15} /></div>
      <Suspense fallback={<div className="scene-loading"><LoaderCircle className="spin" size={24} /></div>}><Kit ref={kit} onHit={strike} visibleDrums={visibleDrums.map(drum => drum.id)} /></Suspense>
    </div>
    <DrumPads active={active} onHit={strike} visible={visibleDrums} />
    <div className="voice-stem-panel" role="group" aria-label="Optional voice stem">
      <div className="voice-stem-heading"><span className="voice-stem-icon"><FileAudio size={17} /></span><div><strong>Optional voice stem</strong><span>MP3 only · synced playback · {jobId ? 'saved with this recording' : 'session playback only'}</span></div></div>
      <div className="voice-stem-controls">
        {voiceFile ? <div className="voice-stem-file"><div><strong title={voiceFile.name}>{voiceFile.name}</strong><span>{voiceSaving ? `UPLOADING TO SERVER · ${voiceUploadPercent}%` : voiceLoading ? 'LOADING VOCAL STEM' : voiceReady ? `${formatTime(voiceDuration || 0)} · ${voicePersisted ? 'SAVED WITH RECORDING' : jobId ? 'NOT SAVED' : 'SESSION ONLY'}` : 'PREPARING AUDIO'}</span></div><button className="icon-button" title="Remove voice stem" aria-label="Remove voice stem" onClick={() => void removeVoice()} disabled={voiceSaving}><X size={15} /></button></div> : <span className="voice-stem-empty">{jobId ? 'Add a vocal MP3 to store it with this recording.' : 'Open a recording to save a vocal MP3 on the server.'}</span>}
        <label className="voice-stem-upload"><input ref={voiceInput} type="file" accept=".mp3,audio/mpeg" aria-label="Choose voice stem MP3" className="sr-only" onChange={event => { chooseVoice(event.target.files?.[0]); event.target.value = ''; }} /><Upload size={15} />{voiceFile ? 'Replace MP3' : 'Choose MP3'}</label>
      </div>
      {(voiceLoading || voiceSaving) && <div className="voice-stem-progress" role="status" aria-label={voiceSaving ? 'Uploading voice stem' : 'Loading voice stem'}><span style={voiceSaving ? { width: `${voiceUploadPercent}%`, animation: 'none' } : undefined} /></div>}
      {voiceError && <p className="voice-stem-error" role="alert">{voiceError}</p>}
    </div>
    <div className="player-tools">
      <details className="player-tool">
        <summary><Volume2 size={15} />Independent mixer</summary>
        <div className="player-tool-content mixer-controls">
          {volumeControls.map(control => <label key={control.label} className="range-control">
            <span>{control.label}<output>{control.value}%</output></span>
            <input type="range" min="0" max="100" step="1" value={control.value}
              onChange={event => control.onChange(Number(event.target.value))}
              aria-label={control.label} />
          </label>)}
        </div>
      </details>
      <details className="player-tool">
        <summary><Activity size={15} />Humanize playback</summary>
        <div className="player-tool-content humanize-controls">
          <label className="range-control"><span>Swing<output>{swing}%</output></span><input type="range" min="0" max="100" value={swing} onChange={event => setSwing(Number(event.target.value))} aria-label="Swing" /></label>
          <label className="range-control"><span>Timing feel<output>{timingMs} ms</output></span><input type="range" min="0" max="30" value={timingMs} onChange={event => setTimingMs(Number(event.target.value))} aria-label="Timing feel" /></label>
          <label className="range-control"><span>Velocity variation<output>{velocityPercent}%</output></span><input type="range" min="0" max="40" value={velocityPercent} onChange={event => setVelocityPercent(Number(event.target.value))} aria-label="Velocity variation" /></label>
          <label className="range-control"><span>Quantization strength<output>{quantizePercent}%</output></span><input type="range" min="0" max="100" value={quantizePercent} onChange={event => setQuantizePercent(Number(event.target.value))} aria-label="Quantization strength" /></label>
          <p>These controls affect playback only. Your original transcription and exports stay unchanged.</p>
        </div>
      </details>
      <section className="player-tool practice-tool" aria-label="Practice mode">
        <h3><Repeat2 size={15} />Practice section</h3>
        <div className="practice-range">
          <label>Start bar<input type="number" min="1" max={measureCount} value={sectionStart}
            onChange={event => setSectionStart(Math.max(1, Math.min(measureCount, Number(event.target.value) || 1)))} /></label>
          <span>to</span>
          <label>End bar<input type="number" min={sectionStart} max={measureCount} value={sectionEnd}
            onChange={event => setSectionEnd(Math.max(sectionStart, Math.min(measureCount, Number(event.target.value) || sectionStart)))} /></label>
        </div>
        <div className="practice-options">
          <label><input type="checkbox" checked={countIn} onChange={event => setCountIn(event.target.checked)} />Count-in</label>
          <label className="range-control ramp-control"><span>Tempo increase<output>{tempoRamp}% / loop</output></span><input type="range" min="0" max="10" value={tempoRamp} onChange={event => setTempoRamp(Number(event.target.value))} aria-label="Tempo increase per loop" /></label>
        </div>
        <div className="practice-footer"><span>Loop {sectionStart}-{sectionEnd} · {sectionProgress} completed · {practiceRate.toFixed(2)}x · saved on this device</span>
          <button className={`secondary-button ${practiceLoop ? 'selected' : ''}`} onClick={() => void togglePractice()} disabled={unlocking}
            aria-pressed={practiceLoop}>{unlocking && practiceLoop ? <LoaderCircle size={14} className="spin" /> : null}{practiceLoop ? 'Stop section loop' : 'Start section loop'}</button>
        </div>
      </section>
    </div>
    <div className="transport">
      <div className="track-heading"><div className="track-info"><span className="track-icon"><Activity size={18} /></span><div><h3 title={sequence ? title : 'Pocket groove'}>{loading ? 'Loading your MIDI...' : sequence ? title.replace(/\.[^.]+$/, '') : 'Pocket groove'}</h3><span>{sequence ? 'YOUR TRANSCRIPTION' : 'BUILT-IN DEMO'}<i />{Math.round(music.bpm)} BPM</span></div></div><span className="track-format">{sequence ? 'MIDI' : 'DEMO'}</span></div>
      <div className="waveform" aria-hidden="true">{bars.map((height, index) => <span key={index} className={index / bars.length <= time / music.duration ? 'heard' : ''} style={{ height: `${height * 100}%` }} />)}<div className="playhead" style={{ left: `${time / music.duration * 100}%` }} /></div>
      <input className="seek-range" type="range" min="0" max={music.duration} step="0.01" value={time} onChange={event => seek(Number(event.target.value))} aria-label="Playback position" aria-valuetext={`${formatTime(time)} of ${formatTime(music.duration)}`} />
      <div className="transport-bottom"><span className="timecode">{formatTime(time)} <span>/ {formatTime(music.duration)}</span></span><div className="play-controls"><button className={`icon-button ${loop ? 'selected' : ''}`} title="Loop playback" aria-label="Loop playback" aria-pressed={loop} onClick={() => setLoop(!loop)}><Repeat2 size={17} /></button><button className="icon-button" title="Restart playback" aria-label="Restart playback" onClick={() => seek(0)}><SkipBack size={18} fill="currentColor" /></button><button className="play-button" aria-label={playing ? 'Pause playback' : 'Play drums'} title={playing ? 'Pause playback' : 'Play drums'} onClick={toggle} disabled={unlocking || loading || Boolean(voiceFile && !voiceReady)}>{unlocking ? <LoaderCircle size={21} className="spin" /> : playing ? <Pause size={21} fill="currentColor" /> : <Play size={21} fill="currentColor" />}</button><button className="icon-button" aria-label={muted ? 'Unmute drums' : 'Mute drums'} title={muted ? 'Unmute drums' : 'Mute drums'} onClick={() => setMuted(!muted)}>{muted ? <VolumeX size={18} /> : <Volume2 size={18} />}</button><label className="speed-select"><span className="sr-only">Playback speed</span><select value={rate} onChange={event => setRate(Number(event.target.value))}><option value={0.5}>0.5x</option><option value={0.75}>0.75x</option><option value={1}>1.0x</option><option value={1.25}>1.25x</option><option value={1.5}>1.5x</option></select></label></div><span className="meter-label"><span className={playing ? 'meter moving' : 'meter'}><i /><i /><i /><i /></span>STEREO</span></div>
      {audioLoadState !== 'idle' && <p className={`audio-load-status ${audioLoadState}`} role="status">{audioLoadState === 'loading' ? <><LoaderCircle size={13} className="spin" />Loading drum sounds...</> : audioLoadState === 'ready' ? 'Audio ready' : 'Audio could not load'}</p>}
      {error && <p className="inline-error" role="alert"><CirclePause size={14} />{error}{audioLoadState === 'error' && <button className="secondary-button" onClick={retryAudio}>Retry audio</button>}</p>}
    </div>
  </section>;
}