import { lazy, Suspense, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { Activity, CirclePause, Headphones, LoaderCircle, Pause, Play, Repeat2, SkipBack, Volume2, VolumeX } from 'lucide-react';
import type { DrumKitHandle } from './DrumKit';
import { demoGroove, drumForNote, drums, type Drum, type Sequence } from './music';
import { formatTime } from './api';
import type { DrumAudio } from './audio';

const Kit = lazy(() => import('./DrumKit').then(module => ({ default: module.DrumKit })));
const demo = demoGroove();

function DrumPads({ active, onHit }: { active: Drum | null; onHit: (drum: Drum) => void }) {
  return <div className="drum-pads">{drums.map(drum => <button key={drum.id} className={`drum-pad ${active === drum.id ? 'is-hit' : ''}`} onClick={() => onHit(drum.id)} title={`Play ${drum.name}`} style={{ '--pad-color': drum.color } as CSSProperties}><span className="pad-dot" />{drum.name}</button>)}</div>;
}

export default function Player({ sequence, title, loading }: { sequence: Sequence | null; title: string; loading: boolean }) {
  const music = sequence || demo;
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [rate, setRate] = useState(1);
  const [muted, setMuted] = useState(false);
  const [loop, setLoop] = useState(false);
  const [active, setActive] = useState<Drum | null>(null);
  const [error, setError] = useState('');
  const [unlocking, setUnlocking] = useState(false);
  const kit = useRef<DrumKitHandle>(null);
  const audio = useRef<DrumAudio | null>(null);
  const position = useRef(0);
  const pulseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loopRef = useRef(loop);
  const volumeRef = useRef(muted);
  const audioPromise = useRef<Promise<DrumAudio> | null>(null);
  useEffect(() => { loopRef.current = loop; }, [loop]);
  useEffect(() => { volumeRef.current = muted; audio.current?.mute(muted); }, [muted]);
  useEffect(() => () => { if (pulseTimer.current) clearTimeout(pulseTimer.current); }, []);

  async function getAudio() {
    audioPromise.current ||= import('./audio').then(async ({ DrumAudio }) => {
      const engine = new DrumAudio();
      audio.current = engine;
      return engine;
    });
    const engine = await audioPromise.current;
    await engine.unlock();
    engine.mute(volumeRef.current);
    return engine;
  }

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
    } catch { setError('Audio could not start. Check your browser audio permissions.'); }
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
    let index = music.hits.findIndex(hit => hit.time >= startPosition - 0.001);
    if (index < 0) index = music.hits.length;
    const visual: { at: number; drum: Drum; velocity: number }[] = [];
    let lastPaint = 0;
    const timer = window.setInterval(() => {
      const now = engine.now();
      const current = startPosition + (now - startTime) * rate;
      if (current >= music.duration) {
        if (loopRef.current) {
          startTime = now; startPosition = 0; index = 0; visual.length = 0;
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
          const at = Math.max(now, startTime + (hit.time - startPosition) / rate);
          engine.hit(hit, at);
          visual.push({ at, drum: drumForNote(hit.midi), velocity: hit.velocity });
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
  }, [playing, rate, music]);

  async function toggle() {
    if (playing) { setPlaying(false); return; }
    setUnlocking(true); setError('');
    try { await getAudio(); setPlaying(true); }
    catch { setError('Audio could not start. Check your browser audio permissions.'); }
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
      <div className="kit-stage-meta"><span>STANDARD / 8-PIECE</span><Headphones size={15} /></div>
      <Suspense fallback={<div className="scene-loading"><LoaderCircle className="spin" size={24} /></div>}><Kit ref={kit} onHit={strike} /></Suspense>
    </div>
    <DrumPads active={active} onHit={strike} />
    <div className="transport">
      <div className="track-heading"><div className="track-info"><span className="track-icon"><Activity size={18} /></span><div><h3 title={sequence ? title : 'Pocket groove'}>{loading ? 'Loading your MIDI...' : sequence ? title.replace(/\.[^.]+$/, '') : 'Pocket groove'}</h3><span>{sequence ? 'YOUR TRANSCRIPTION' : 'BUILT-IN DEMO'}<i />{Math.round(music.bpm)} BPM</span></div></div><span className="track-format">{sequence ? 'MIDI' : 'DEMO'}</span></div>
      <div className="waveform" aria-hidden="true">{bars.map((height, index) => <span key={index} className={index / bars.length <= time / music.duration ? 'heard' : ''} style={{ height: `${height * 100}%` }} />)}<div className="playhead" style={{ left: `${time / music.duration * 100}%` }} /></div>
      <input className="seek-range" type="range" min="0" max={music.duration} step="0.01" value={time} onChange={event => seek(Number(event.target.value))} aria-label="Playback position" aria-valuetext={`${formatTime(time)} of ${formatTime(music.duration)}`} />
      <div className="transport-bottom"><span className="timecode">{formatTime(time)} <span>/ {formatTime(music.duration)}</span></span><div className="play-controls"><button className={`icon-button ${loop ? 'selected' : ''}`} title="Loop playback" aria-label="Loop playback" aria-pressed={loop} onClick={() => setLoop(!loop)}><Repeat2 size={17} /></button><button className="icon-button" title="Restart playback" aria-label="Restart playback" onClick={() => seek(0)}><SkipBack size={18} fill="currentColor" /></button><button className="play-button" aria-label={playing ? 'Pause playback' : 'Play drums'} title={playing ? 'Pause playback' : 'Play drums'} onClick={toggle} disabled={unlocking || loading}>{unlocking ? <LoaderCircle size={21} className="spin" /> : playing ? <Pause size={21} fill="currentColor" /> : <Play size={21} fill="currentColor" />}</button><button className="icon-button" aria-label={muted ? 'Unmute drums' : 'Mute drums'} title={muted ? 'Unmute drums' : 'Mute drums'} onClick={() => setMuted(!muted)}>{muted ? <VolumeX size={18} /> : <Volume2 size={18} />}</button><label className="speed-select"><span className="sr-only">Playback speed</span><select value={rate} onChange={event => setRate(Number(event.target.value))}><option value={0.5}>0.5x</option><option value={0.75}>0.75x</option><option value={1}>1.0x</option><option value={1.25}>1.25x</option><option value={1.5}>1.5x</option></select></label></div><span className="meter-label"><span className={playing ? 'meter moving' : 'meter'}><i /><i /><i /><i /></span>STEREO</span></div>
      {error && <p className="inline-error" role="alert"><CirclePause size={14} />{error}</p>}
    </div>
  </section>;
}