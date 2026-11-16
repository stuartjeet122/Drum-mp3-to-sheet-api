import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';
import type { Drum } from './music';

export interface DrumKitHandle { hit: (drum: Drum, velocity?: number) => void }

const instruments: { id: Drum; name: string; x: number; y: number; radius: number; cymbal?: boolean }[] = [
  { id: 'crash', name: 'Crash cymbal', x: 184, y: 88, radius: 61, cymbal: true },
  { id: 'ride', name: 'Ride cymbal', x: 556, y: 91, radius: 70, cymbal: true },
  { id: 'hihat', name: 'Hi-hat', x: 113, y: 232, radius: 52, cymbal: true },
  { id: 'tom1', name: 'High tom', x: 267, y: 160, radius: 49 },
  { id: 'tom2', name: 'Mid tom', x: 379, y: 151, radius: 55 },
  { id: 'floor', name: 'Floor tom', x: 573, y: 273, radius: 70 },
  { id: 'snare', name: 'Snare', x: 263, y: 274, radius: 59 },
  { id: 'kick', name: 'Kick', x: 408, y: 329, radius: 91 },
];

export const DrumKit = forwardRef<DrumKitHandle, { onHit: (drum: Drum) => void; visibleDrums: Drum[] }>(
  function DrumKit({ onHit, visibleDrums }, ref) {
  const [hits, setHits] = useState<Partial<Record<Drum, number>>>({});
  const versions = useRef<Partial<Record<Drum, number>>>({});
  const timers = useRef<Partial<Record<Drum, ReturnType<typeof setTimeout>>>>({});
  const handler = useRef(onHit);
  useEffect(() => { handler.current = onHit; }, [onHit]);
  useEffect(() => () => {
    Object.values(timers.current).forEach(timer => { if (timer) clearTimeout(timer); });
  }, []);

  const hit = useCallback((drum: Drum) => {
    const version = (versions.current[drum] || 0) + 1;
    versions.current[drum] = version;
    setHits(previous => ({ ...previous, [drum]: version }));
    if (timers.current[drum]) clearTimeout(timers.current[drum]);
    timers.current[drum] = setTimeout(() => {
      setHits(previous => previous[drum] === version ? { ...previous, [drum]: 0 } : previous);
    }, 360);
  }, []);

  useImperativeHandle(ref, () => ({ hit }), [hit]);

  function activate(drum: Drum) {
    hit(drum);
    handler.current(drum);
  }

  return <div className="kit-scene">
    <svg className="top-kit-svg" viewBox="0 0 680 430" role="group" aria-label={`Interactive top view of an acoustic drum kit with ${visibleDrums.length} pieces`}>
      <defs>
        <radialGradient id="head-surface" cx="38%" cy="28%" r="76%">
          <stop offset="0" stopColor="#f4f8ff" />
          <stop offset=".58" stopColor="#c4d6eb" />
          <stop offset=".88" stopColor="#91a8c2" />
          <stop offset="1" stopColor="#536a84" />
        </radialGradient>
        <radialGradient id="kick-surface" cx="38%" cy="28%" r="78%">
          <stop offset="0" stopColor="#eaf4ff" />
          <stop offset=".7" stopColor="#b5cbe1" />
          <stop offset="1" stopColor="#728aa5" />
        </radialGradient>
        <radialGradient id="cymbal-surface" cx="38%" cy="31%" r="73%">
          <stop offset="0" stopColor="#f2fbff" />
          <stop offset=".35" stopColor="#8be4ff" />
          <stop offset=".78" stopColor="#397da5" />
          <stop offset="1" stopColor="#173653" />
        </radialGradient>
        <linearGradient id="rim-surface" x1="0" y1="0" x2="1" y2="1">
          <stop stopColor="#edf7ff" />
          <stop offset=".38" stopColor="#66839f" />
          <stop offset=".68" stopColor="#cfe4f8" />
          <stop offset="1" stopColor="#3c526d" />
        </linearGradient>
        <filter id="drum-shadow" x="-35%" y="-35%" width="170%" height="180%">
          <feDropShadow dx="0" dy="7" stdDeviation="7" floodColor="#000" floodOpacity=".42" />
        </filter>
        <pattern id="head-grain" width="12" height="12" patternUnits="userSpaceOnUse">
          <circle cx="2" cy="3" r=".65" fill="#344b64" opacity=".18" />
          <circle cx="9" cy="8" r=".5" fill="#fff" opacity=".3" />
        </pattern>
      </defs>
      <ellipse cx="340" cy="407" rx="270" ry="15" fill="#000" opacity=".2" />
      {instruments.filter(instrument => visibleDrums.includes(instrument.id)).map(instrument => {
        const version = hits[instrument.id] || 0;
        const active = version > 0;
        const { id, name, x, y, radius, cymbal } = instrument;
        return <g
          key={id}
          className={`kit-instrument ${cymbal ? 'kit-cymbal' : 'kit-drum'} ${active ? 'is-hit' : ''}`}
          role="button"
          tabIndex={0}
          aria-label={`Play ${name}`}
          onClick={() => activate(id)}
          onKeyDown={event => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              activate(id);
            }
          }}
          transform={`translate(${x} ${y})`}
        >
          {cymbal ? <>
            <circle className="cymbal-shadow" r={radius + 5} />
            <circle className="cymbal-disc" r={radius} />
            <circle className="cymbal-lathe" r={radius * .82} />
            <circle className="cymbal-lathe" r={radius * .67} />
            <circle className="cymbal-lathe" r={radius * .51} />
            <circle className="cymbal-bell" r={radius * .2} />
            <circle className="cymbal-bolt" r={4} />
            <text className="cymbal-label" y={radius * .59}>{id.toUpperCase()}</text>
          </> : <>
            <circle className="drum-shell" r={radius + 5} />
            <circle className="drum-rim" r={radius} />
            <circle className={`drum-head ${id === 'kick' ? 'kick-head' : ''}`} r={radius - 7} />
            <circle className="drum-grain" r={radius - 8} />
            {id === 'kick' ? <>
              <circle className="kick-port" cx={radius * .38} cy={radius * .39} r={radius * .11} />
              <text className="kick-brand" y={-radius * .2}>BACKBEAT</text>
              <text className="kick-subbrand" y={-radius * .02}>DRUM STUDIO</text>
            </> : <>
              <circle className="drum-center" r={radius * .12} />
              {Array.from({ length: 8 }, (_, index) => {
                const angle = index * Math.PI / 4;
                return <circle key={index} className="rim-lug" cx={Math.cos(angle) * (radius + 1)} cy={Math.sin(angle) * (radius + 1)} r="3.1" />;
              })}
            </>}
          </>}
          {active && <circle key={version} className="hit-ripple" r={radius * .56} />}
          {active && <circle key={`second-${version}`} className="hit-ripple hit-ripple-late" r={radius * .38} />}
        </g>;
      })}
      <text className="kit-label" x="340" y="426">TOP VIEW · ACOUSTIC KIT</text>
    </svg>
  </div>;
  },
);
