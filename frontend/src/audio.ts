import * as Tone from 'tone';
import { drumForNote, type Hit } from './music';

const variations = (name: string) => Array.from(
  { length: 3 },
  (_, index) => `${name}-${String(index + 1).padStart(3, '0')}-Natural.mp3`,
);

const sampleGroups = {
  kickSoft: variations('KickA-Soft'),
  kickMedium: variations('KickA-Med'),
  kickHard: variations('KickA-Hard'),
  snareSoft: variations('SnareA-Soft'),
  snareMedium: variations('SnareA-Med'),
  snareHard: variations('SnareA-Hard'),
  sideStick: variations('Side-Stick'),
  hatClosed: variations('Hat-Closed'),
  hatPedal: variations('Hat-Pedal'),
  hatOpen: variations('Hat-OpenMed'),
  tom1: variations('Tom1'),
  tom2: variations('Tom2'),
  tom3: variations('Tom3'),
  crash: variations('CrashA'),
  ride: variations('Ride'),
  rideBell: variations('RideBell'),
};

const sampleUrls: Record<string, string> = {};
for (const files of Object.values(sampleGroups)) {
  for (const file of files) {
    sampleUrls[file] = `${import.meta.env.BASE_URL}samples/drums/${file}`;
  }
}

type SampleGroup = keyof typeof sampleGroups;

export class DrumAudio {
  private lastHits = new Map<string, number>();
  private roundRobin = new Map<SampleGroup, number>();
  private openHat: Tone.Player | null = null;
  private output = new Tone.Volume(-6).toDestination();
  private limiter = new Tone.Limiter(-1.5).connect(this.output);
  private drumChannel = new Tone.Volume(0).connect(this.limiter);
  private voiceChannel = new Tone.Volume(-4).connect(this.limiter);
  private countInSynth = new Tone.Synth({
    oscillator: { type: 'sine' },
    envelope: { attack: 0.002, decay: 0.04, sustain: 0, release: 0.02 },
  }).connect(this.output);
  private sampleLoadError: Error | null = null;
  private rejectSampleLoad: ((error: Error) => void) | null = null;
  private players = new Tone.Players(sampleUrls, {
    fadeOut: 0.035,
    onerror: error => {
      this.sampleLoadError = error;
      this.rejectSampleLoad?.(error);
    },
  }).connect(this.drumChannel);
  private voicePlayer: Tone.Player | null = null;
  private voiceLoadId = 0;
  private loadPromise: Promise<void> | null = null;

  async unlock() {
    await Tone.start();
    if (this.sampleLoadError) throw this.sampleLoadError;
    this.loadPromise ||= new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.rejectSampleLoad = null;
        reject(new Error('Drum samples took too long to load.'));
      }, 20_000);
      this.rejectSampleLoad = error => {
        clearTimeout(timeout);
        reject(error);
      };
      void Tone.loaded().then(() => {
        clearTimeout(timeout);
        if (this.sampleLoadError) reject(this.sampleLoadError);
        else resolve();
      }, error => {
        clearTimeout(timeout);
        reject(error);
      });
    }).then(() => {
      this.rejectSampleLoad = null;
    }).catch(error => {
      this.rejectSampleLoad = null;
      this.loadPromise = null;
      throw error;
    });
    await this.loadPromise;
  }

  now() { return Tone.now(); }
  mute(value: boolean) { this.drumChannel.mute = value; }
  private setLevel(channel: Tone.Volume, percent: number) {
    const value = Math.max(0, Math.min(100, percent));
    channel.volume.value = value === 0 ? -60 : 20 * Math.log10(value / 100);
  }
  drumLevel(percent: number) { this.setLevel(this.drumChannel, percent); }
  voiceLevel(percent: number) { this.setLevel(this.voiceChannel, percent); }
  masterLevel(percent: number) { this.setLevel(this.output, percent); }

  async loadVoice(url: string | null): Promise<number | null> {
    const loadId = ++this.voiceLoadId;
    this.voicePlayer?.stop();
    this.voicePlayer?.dispose();
    this.voicePlayer = null;
    if (!url) return null;

    const player = new Tone.Player({ fadeOut: 0.035 }).connect(this.voiceChannel);
    try {
      await player.load(url);
    } catch (error) {
      player.dispose();
      throw error;
    }
    if (loadId !== this.voiceLoadId) {
      player.dispose();
      return null;
    }
    this.voicePlayer = player;
    return player.buffer.duration;
  }

  voiceRate(rate: number) {
    if (this.voicePlayer) this.voicePlayer.playbackRate = rate;
  }

  playVoice(time: number, offset: number) {
    this.voicePlayer?.start(time, offset);
  }

  restartVoice(time: number, offset: number) {
    if (!this.voicePlayer) return;
    this.voicePlayer.stop(time);
    this.voicePlayer.start(time + 0.01, offset);
  }

  countIn(time: number, accented: boolean) {
    this.countInSynth.triggerAttackRelease(accented ? 'C6' : 'G5', '32n', time, 0.35);
  }

  private play(group: SampleGroup, time: number): Tone.Player {
    const files = sampleGroups[group];
    const index = this.roundRobin.get(group) || 0;
    this.roundRobin.set(group, index + 1);
    const player = this.players.player(files[index % files.length]);
    player.start(time);
    return player;
  }

  hit(hit: Hit, time = Tone.now()) {
    const velocity = Math.max(0.05, Math.min(1, hit.velocity));
    const level = velocity < 0.4 ? 'Soft' : velocity < 0.76 ? 'Medium' : 'Hard';
    const drum = drumForNote(hit.midi);
    if (!drum) return;
    time = Math.max(time, (this.lastHits.get(drum) ?? -Infinity) + 0.001);
    this.lastHits.set(drum, time);

    switch (drum) {
      case 'kick': this.play(`kick${level}`, time); break;
      case 'snare':
        this.play(hit.midi === 37 ? 'sideStick' : `snare${level}`, time);
        break;
      case 'hihat':
        if (hit.midi === 46) this.openHat = this.play('hatOpen', time);
        else {
          this.openHat?.stop(time);
          this.openHat = null;
          this.play(hit.midi === 44 ? 'hatPedal' : 'hatClosed', time);
        }
        break;
      case 'tom1': this.play('tom1', time); break;
      case 'tom2': this.play('tom2', time); break;
      case 'floor': this.play('tom3', time); break;
      case 'crash': this.play('crash', time); break;
      case 'ride': this.play(hit.midi === 53 ? 'rideBell' : 'ride', time); break;
    }
  }

  stop() {
    this.players.stopAll();
    this.voicePlayer?.stop();
    this.openHat = null;
  }

  dispose() {
    this.stop();
    this.voiceLoadId++;
    this.voicePlayer?.dispose();
    this.voicePlayer = null;
    this.players.dispose();
    this.countInSynth.dispose();
    this.drumChannel.dispose();
    this.voiceChannel.dispose();
    this.limiter.dispose();
    this.output.dispose();
  }
}
