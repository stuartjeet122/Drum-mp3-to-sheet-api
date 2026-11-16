export type Drum = 'kick' | 'snare' | 'hihat' | 'tom1' | 'tom2' | 'floor' | 'crash' | 'ride';
export interface Hit { time: number; duration: number; midi: number; velocity: number }
export interface Sequence { hits: Hit[]; duration: number; bpm: number }

export const drums: { id: Drum; name: string; color: string; note: number }[] = [
  { id: 'kick', name: 'Kick', color: '#d5ed83', note: 36 },
  { id: 'snare', name: 'Snare', color: '#f0a887', note: 38 },
  { id: 'hihat', name: 'Hi-hat', color: '#d7bd77', note: 42 },
  { id: 'tom1', name: 'High tom', color: '#a6c5dc', note: 48 },
  { id: 'tom2', name: 'Mid tom', color: '#85bfae', note: 45 },
  { id: 'floor', name: 'Floor tom', color: '#7fa7cd', note: 41 },
  { id: 'crash', name: 'Crash', color: '#ddc48a', note: 49 },
  { id: 'ride', name: 'Ride', color: '#c4aa7a', note: 51 },
];

export function drumForNote(note: number): Drum | null {
  if ([35, 36].includes(note)) return 'kick';
  if ([37, 38, 40].includes(note)) return 'snare';
  if ([42, 44, 46].includes(note)) return 'hihat';
  if ([48, 50].includes(note)) return 'tom1';
  if ([45, 47].includes(note)) return 'tom2';
  if ([41, 43].includes(note)) return 'floor';
  if ([49, 52, 55, 57].includes(note)) return 'crash';
  if ([51, 53, 59].includes(note)) return 'ride';
  return null;
}

export function demoGroove(): Sequence {
  const bpm = 92;
  const eighth = 60 / bpm / 2;
  const hits: Hit[] = [];
  for (let step = 0; step < 64; step++) {
    const time = step * eighth;
    hits.push({ time, midi: 42, velocity: step % 2 ? 0.45 : 0.65, duration: 0.08 });
    if ([0, 3, 4].includes(step % 8)) hits.push({ time, midi: 36, velocity: 0.85, duration: 0.15 });
    if (step % 4 === 2) hits.push({ time, midi: 38, velocity: 0.82, duration: 0.12 });
    if (step === 0 || step === 32) hits.push({ time, midi: 49, velocity: 0.55, duration: 0.5 });
    if ([29, 30, 31, 61, 62, 63].includes(step)) hits.push({ time, midi: step % 2 ? 45 : 48, velocity: 0.72, duration: 0.15 });
  }
  return { hits: hits.sort((left, right) => left.time - right.time), duration: 64 * eighth, bpm };
}

export async function readMidi(buffer: ArrayBuffer): Promise<Sequence> {
  const { Midi } = await import('@tonejs/midi');
  const midi = new Midi(buffer);
  const tracks = midi.tracks.some(track => track.channel === 9)
    ? midi.tracks.filter(track => track.channel === 9) : midi.tracks;
  const hits = tracks.flatMap(track => track.notes.map(note => ({
    time: note.time, duration: note.duration, midi: note.midi, velocity: note.velocity,
  }))).filter(hit => drumForNote(hit.midi) !== null)
    .sort((left, right) => left.time - right.time);
  if (!hits.length) throw new Error('This MIDI contains no playable drum notes.');
  return { hits, duration: Math.max(midi.duration, hits[hits.length - 1].time + 0.5), bpm: midi.header.tempos[0]?.bpm || 120 };
}