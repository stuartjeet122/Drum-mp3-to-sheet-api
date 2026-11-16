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
  private players = new Tone.Players(sampleUrls, { fadeOut: 0.035 }).connect(this.drumChannel);
  private voicePlayer: Tone.Player | null = null;
  private voiceLoadId = 0;

  async unlock() {
    await Tone.start();
    await Tone.loaded();
  }

  now() { return Tone.now(); }
  mute(value: boolean) { this.drumChannel.mute = value; }

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
}
