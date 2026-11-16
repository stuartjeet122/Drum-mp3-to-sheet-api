import * as Tone from 'tone';
import { drumForNote, type Hit } from './music';

export class DrumAudio {
  private lastHits = new Map<string, number>();
  private output = new Tone.Volume(-6).toDestination();
  private limiter = new Tone.Limiter(-1.5).connect(this.output);
  private kick = new Tone.MembraneSynth({
    pitchDecay: 0.055, octaves: 8,
    envelope: { attack: 0.001, decay: 0.38, sustain: 0, release: 0.08 },
  }).connect(this.limiter);
  private kickBeater = new Tone.NoiseSynth({
    noise: { type: 'pink' },
    envelope: { attack: 0.001, decay: 0.022, sustain: 0, release: 0.012 },
  }).connect(new Tone.Filter(1800, 'lowpass').connect(this.limiter));
  private snareBody = new Tone.MembraneSynth({
    pitchDecay: 0.012, octaves: 2,
    envelope: { attack: 0.001, decay: 0.12, sustain: 0, release: 0.035 },
  }).connect(this.limiter);
  private snareWire = new Tone.NoiseSynth({
    noise: { type: 'white' },
    envelope: { attack: 0.001, decay: 0.19, sustain: 0, release: 0.045 },
  }).connect(new Tone.Filter(2100, 'bandpass').connect(this.limiter));
  private hats = new Tone.NoiseSynth({
    noise: { type: 'white' },
    envelope: { attack: 0.001, decay: 0.045, sustain: 0, release: 0.025 },
  }).connect(new Tone.Filter(7200, 'highpass').connect(this.limiter));
  private toms = new Tone.PolySynth(Tone.MembraneSynth, {
    pitchDecay: 0.035, octaves: 3,
    envelope: { attack: 0.001, decay: 0.28, sustain: 0, release: 0.08 },
  }).connect(this.limiter);
  private cymbals = new Tone.PolySynth(Tone.MetalSynth, {
    envelope: { attack: 0.001, decay: 0.85, release: 0.5 },
    harmonicity: 5.1, modulationIndex: 32, resonance: 4200, octaves: 1.5, volume: -18,
  }).connect(this.limiter);

  async unlock() { await Tone.start(); }
  now() { return Tone.now(); }
  mute(value: boolean) { this.output.mute = value; }
  hit(hit: Hit, time = Tone.now()) {
    const velocity = Math.max(0.05, Math.min(1, hit.velocity));
    const accent = Math.pow(velocity, 1.25);
    const drum = drumForNote(hit.midi);
    time = Math.max(time, (this.lastHits.get(drum) ?? -Infinity) + 0.001);
    this.lastHits.set(drum, time);
    switch (drum) {
      case 'kick':
        this.kick.triggerAttackRelease('C1', '8n', time, accent);
        this.kickBeater.triggerAttackRelease('32n', time, accent * 0.48);
        break;
      case 'snare':
        this.snareBody.triggerAttackRelease('G2', '16n', time, accent * 0.5);
        this.snareWire.triggerAttackRelease('16n', time, accent * 0.86);
        break;
      case 'hihat': {
        const open = hit.midi === 46;
        this.hats.triggerAttackRelease(open ? '8n' : '32n', time, accent * (open ? 0.48 : 0.38));
        break;
      }
      case 'tom1': this.toms.triggerAttackRelease('D3', '16n', time, accent); break;
      case 'tom2': this.toms.triggerAttackRelease('A2', '16n', time, accent); break;
      case 'floor': this.toms.triggerAttackRelease('E2', '16n', time, accent); break;
      case 'crash': this.cymbals.triggerAttackRelease('C3', '8n', time, accent * 0.62); break;
      case 'ride': this.cymbals.triggerAttackRelease('G4', '32n', time, accent * 0.4); break;
    }
  }
  stop() {
    this.kick.triggerRelease();
    this.kickBeater.triggerRelease();
    this.snareBody.triggerRelease();
    this.snareWire.triggerRelease();
    this.hats.triggerRelease();
    this.toms.releaseAll();
    this.cymbals.releaseAll();
  }
}