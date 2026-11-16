import * as Tone from 'tone';
import { drumForNote, type Hit } from './music';

export class DrumAudio {
  private lastHits = new Map<string, number>();
  private output = new Tone.Volume(-8).toDestination();
  private limiter = new Tone.Limiter(-1).connect(this.output);
  private kick = new Tone.MembraneSynth({ pitchDecay: 0.04, octaves: 7, envelope: { attack: 0.001, decay: 0.25, sustain: 0, release: 0.1 } }).connect(this.limiter);
  private snare = new Tone.NoiseSynth({ noise: { type: 'white' }, envelope: { attack: 0.001, decay: 0.13, sustain: 0, release: 0.05 } }).connect(new Tone.Filter(1600, 'highpass').connect(this.limiter));
  private hats = new Tone.NoiseSynth({ noise: { type: 'white' }, envelope: { attack: 0.001, decay: 0.035, sustain: 0, release: 0.025 } }).connect(new Tone.Filter(8000, 'highpass').connect(this.limiter));
  private toms = new Tone.PolySynth(Tone.MembraneSynth, { pitchDecay: 0.04, octaves: 3, envelope: { attack: 0.001, decay: 0.2, sustain: 0, release: 0.1 } }).connect(this.limiter);
  private cymbals = new Tone.PolySynth(Tone.MetalSynth, { envelope: { attack: 0.001, decay: 0.55, release: 0.1 }, harmonicity: 5.1, modulationIndex: 32, resonance: 4000, octaves: 1.5, volume: -19 }).connect(this.limiter);

  async unlock() { await Tone.start(); }
  now() { return Tone.now(); }
  mute(value: boolean) { this.output.mute = value; }
  hit(hit: Hit, time = Tone.now()) {
    const velocity = Math.max(0.05, Math.min(1, hit.velocity));
    const drum = drumForNote(hit.midi);
    time = Math.max(time, (this.lastHits.get(drum) ?? -Infinity) + 0.001);
    this.lastHits.set(drum, time);
    switch (drum) {
      case 'kick': this.kick.triggerAttackRelease('C1', '16n', time, velocity); break;
      case 'snare': this.snare.triggerAttackRelease('16n', time, velocity * 0.65); break;
      case 'hihat': this.hats.triggerAttackRelease(hit.midi === 46 ? '8n' : '32n', time, velocity * 0.35); break;
      case 'tom1': this.toms.triggerAttackRelease('D3', '16n', time, velocity); break;
      case 'tom2': this.toms.triggerAttackRelease('A2', '16n', time, velocity); break;
      case 'floor': this.toms.triggerAttackRelease('E2', '16n', time, velocity); break;
      case 'crash': this.cymbals.triggerAttackRelease('C3', '8n', time, velocity * 0.5); break;
      case 'ride': this.cymbals.triggerAttackRelease('G4', '32n', time, velocity * 0.3); break;
    }
  }
  stop() {
    this.kick.triggerRelease();
    this.snare.triggerRelease();
    this.hats.triggerRelease();
    this.toms.releaseAll();
    this.cymbals.releaseAll();
  }
}