"""Expressive or grid-aligned General MIDI drum export."""

from pathlib import Path

import pretty_midi

from engine.notation import NotationExportError
from engine.notation.drum_mapping import get_drum_spec
from engine.rhythm.schemas import MusicalTranscription


def export_to_midi(
    transcription: MusicalTranscription,
    output_path: Path,
    use_quantized: bool = False,
) -> Path:
    """Write channel-10 percussion; original onset timing is the default."""
    grid = transcription.beat_grid
    midi = pretty_midi.PrettyMIDI(initial_tempo=grid.bpm, resolution=960)
    midi.time_signature_changes.append(pretty_midi.TimeSignature(
        grid.time_signature_numerator, grid.time_signature_denominator, 0,
    ))
    drums = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    for event in transcription.events:
        spec = get_drum_spec(event.instrument)
        start = event.quantized_time_sec if use_quantized else event.time_sec
        gate = max(0.011, min(0.15, event.duration_quarters * 60 / grid.bpm))
        drums.notes.append(pretty_midi.Note(
            velocity=max(1, min(127, event.velocity)), pitch=spec.gm_midi_note,
            start=start, end=start + gate,
        ))
    drums.notes.sort(key=lambda hit: (hit.start, hit.pitch))
    midi.instruments.append(drums)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        midi.write(str(output_path))
    except (OSError, ValueError) as exc:
        raise NotationExportError(f"Cannot write MIDI to {output_path}: {exc}") from exc
    return output_path