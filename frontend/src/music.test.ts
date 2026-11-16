import { expect, it } from 'vitest';
import { demoGroove, drumForNote } from './music';

it('maps General MIDI percussion to the visible drum kit', () => {
  expect(drumForNote(36)).toBe('kick');
  expect(drumForNote(38)).toBe('snare');
  expect(drumForNote(46)).toBe('hihat');
  expect(drumForNote(48)).toBe('tom1');
  expect(drumForNote(45)).toBe('tom2');
  expect(drumForNote(41)).toBe('floor');
  expect(drumForNote(49)).toBe('crash');
  expect(drumForNote(51)).toBe('ride');
  expect(drumForNote(53)).toBe('ride');
  expect(drumForNote(59)).toBe('ride');
  expect(drumForNote(75)).toBeNull();
  expect(drumForNote(39)).toBeNull();
});

it('provides a real timed demo sequence without submitting an API job', () => {
  const sequence = demoGroove();
  expect(sequence.bpm).toBe(92);
  expect(sequence.hits.length).toBeGreaterThan(100);
  expect(sequence.hits.every(hit => hit.time >= 0 && hit.time < sequence.duration)).toBe(true);
  expect(sequence.hits.map(hit => hit.time)).toEqual(sequence.hits.map(hit => hit.time).sort((left, right) => left - right));
});