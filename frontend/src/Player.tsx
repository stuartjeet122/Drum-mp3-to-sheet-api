import { lazy, Suspense, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { Activity, CirclePause, FileAudio, Headphones, LoaderCircle, Pause, Play, Repeat2, SkipBack, Upload, Volume2, VolumeX, X } from 'lucide-react';
import type { DrumKitHandle } from './DrumKit';
import { demoGroove, drumForNote, drums, type Drum, type Sequence } from './music';
import { deleteVoiceStem, downloadVoiceStem, formatTime, uploadVoiceStem } from './api';
import type { DrumAudio } from './audio';

const Kit = lazy(() => import('./DrumKit').then(module => ({ default: module.DrumKit })));
const demo = demoGroove();

function DrumPads({ active, onHit, visible }: { active: Drum | null; onHit: (drum: Drum) => void; visible: typeof drums }) {
  return <div className="drum-pads" style={{ '--pad-count': visible.length } as CSSProperties}>{visible.map(drum => <button key={drum.id} className={`drum-pad ${active === drum.id ? 'is-hit' : ''}`} onClick={() => onHit(drum.id)} title={`Play ${drum.name}`} style={{ '--pad-color': drum.color } as CSSProperties}><span className="pad-dot" />{drum.name}</button>)}</div>;
}

export default function Player({
  sequence, title, loading, jobId, voiceFilename, onVoiceFilenameChange,
}: {
  sequence: Sequence | null;
  title: string;
  loading: boolean;
  jobId: string | null;
  voiceFilename: string | null;
  onVoiceFilenameChange: (filename: string | null) => void;
}) {
  const music = sequence || demo;
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
  useEffect(() => { volumeRef.current = muted; audio.current?.mute(muted); }, [muted]);
  useEffect(() => () => {
    if (pulseTimer.current) clearTimeout(pulseTimer.current);
    voiceRequest.current?.abort();
    audio.current?.stop();
    void audio.current?.loadVoice(null);
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
    await engine.unlock();
    engine.mute(volumeRef.current);
    return engine;
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
    try {
      const engine = await getAudio();
      const note = drums.find(item => item.id === drum)!.note;
      engine.hit({ time: 0, duration: 0.1, midi: note, velocity: 0.8 });
      pulse(drum);
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
    let startTime = engine.now();
    let startPosition = position.current;
    engine.voiceRate(rate);
    if (voiceUrl && voiceReady) engine.playVoice(startTime, startPosition);
    let index = music.hits.findIndex(hit => hit.time >= startPosition - 0.001);
    if (index < 0) index = music.hits.length;
    const visual: { at: number; drum: Drum; velocity: number }[] = [];
    let lastPaint = 0;
    const timer = window.setInterval(() => {
      const now = engine.now();
      const current = startPosition + (now - startTime) * rate;
      if (current >= music.duration) {
        if (loopRef.current) {
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
      const elapsed = startPosition + (now - startTime) * rate;
      try {
        while (index < music.hits.length && music.hits[index].time <= elapsed + 0.06 * rate) {
          const hit = music.hits[index++];
          const drum = drumForNote(hit.midi);
          if (!drum) continue;
          const at = Math.max(now, startTime + (hit.time - startPosition) / rate);
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
  }, [playing, rate, music, voiceUrl, voiceReady]);

  async function toggle() {
    if (playing) { setPlaying(false); return; }
    setUnlocking(true); setError('');
    try { await getAudio(); setPlaying(true); }
    catch { setError('Drum samples could not load or audio could not start. Check your connection and browser audio permissions.'); }
    finally { setUnlocking(false); }
  }

  function seek(value: number) {
    setPlaying(false); position.current = value; setTime(value);
  }

  const bars = useMemo(() => {
    const bins = Array<number>(92).fill(0);
    for (const hit of music.hits) {
      const bin = Math.min(91, Math.floor(hit.time / music.duration * 92));
      bins[bin] += hit.velocity * (drumForNote(hit.midi) === 'kick' ? 1.8 : 1);
    }
    return bins.map(value => Math.max(0.08, Math.min(1, value / 2.8)));
  }, [music]);

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
    <div className="transport">
      <div className="track-heading"><div className="track-info"><span className="track-icon"><Activity size={18} /></span><div><h3 title={sequence ? title : 'Pocket groove'}>{loading ? 'Loading your MIDI...' : sequence ? title.replace(/\.[^.]+$/, '') : 'Pocket groove'}</h3><span>{sequence ? 'YOUR TRANSCRIPTION' : 'BUILT-IN DEMO'}<i />{Math.round(music.bpm)} BPM</span></div></div><span className="track-format">{sequence ? 'MIDI' : 'DEMO'}</span></div>
      <div className="waveform" aria-hidden="true">{bars.map((height, index) => <span key={index} className={index / bars.length <= time / music.duration ? 'heard' : ''} style={{ height: `${height * 100}%` }} />)}<div className="playhead" style={{ left: `${time / music.duration * 100}%` }} /></div>
      <input className="seek-range" type="range" min="0" max={music.duration} step="0.01" value={time} onChange={event => seek(Number(event.target.value))} aria-label="Playback position" aria-valuetext={`${formatTime(time)} of ${formatTime(music.duration)}`} />
      <div className="transport-bottom"><span className="timecode">{formatTime(time)} <span>/ {formatTime(music.duration)}</span></span><div className="play-controls"><button className={`icon-button ${loop ? 'selected' : ''}`} title="Loop playback" aria-label="Loop playback" aria-pressed={loop} onClick={() => setLoop(!loop)}><Repeat2 size={17} /></button><button className="icon-button" title="Restart playback" aria-label="Restart playback" onClick={() => seek(0)}><SkipBack size={18} fill="currentColor" /></button><button className="play-button" aria-label={playing ? 'Pause playback' : 'Play drums'} title={playing ? 'Pause playback' : 'Play drums'} onClick={toggle} disabled={unlocking || loading || Boolean(voiceFile && !voiceReady)}>{unlocking ? <LoaderCircle size={21} className="spin" /> : playing ? <Pause size={21} fill="currentColor" /> : <Play size={21} fill="currentColor" />}</button><button className="icon-button" aria-label={muted ? 'Unmute drums' : 'Mute drums'} title={muted ? 'Unmute drums' : 'Mute drums'} onClick={() => setMuted(!muted)}>{muted ? <VolumeX size={18} /> : <Volume2 size={18} />}</button><label className="speed-select"><span className="sr-only">Playback speed</span><select value={rate} onChange={event => setRate(Number(event.target.value))}><option value={0.5}>0.5x</option><option value={0.75}>0.75x</option><option value={1}>1.0x</option><option value={1.25}>1.25x</option><option value={1.5}>1.5x</option></select></label></div><span className="meter-label"><span className={playing ? 'meter moving' : 'meter'}><i /><i /><i /><i /></span>STEREO</span></div>
      {error && <p className="inline-error" role="alert"><CirclePause size={14} />{error}</p>}
    </div>
  </section>;
}