"""Canonical General MIDI pitches and unpitched percussion staff positions."""

from dataclasses import dataclass

from engine.models.base import DrumInstrument
from engine.notation import NotationExportError


@dataclass(frozen=True)
class DrumNotationSpec:
    gm_midi_note: int
    staff_step: str
    staff_octave: int
    notehead: str
    stem_direction: str


_SPECS = {
    DrumInstrument.KICK: DrumNotationSpec(36, "F", 4, "normal", "down"),
    DrumInstrument.SNARE: DrumNotationSpec(38, "C", 5, "normal", "up"),
    DrumInstrument.CLOSED_HIHAT: DrumNotationSpec(42, "G", 5, "x", "up"),
    DrumInstrument.PEDAL_HIHAT: DrumNotationSpec(44, "D", 4, "x", "down"),
    DrumInstrument.OPEN_HIHAT: DrumNotationSpec(46, "G", 5, "circle-x", "up"),
    DrumInstrument.LOW_TOM: DrumNotationSpec(45, "A", 4, "normal", "up"),
    DrumInstrument.MID_TOM: DrumNotationSpec(47, "D", 5, "normal", "up"),
    DrumInstrument.HIGH_TOM: DrumNotationSpec(50, "E", 5, "normal", "up"),
    DrumInstrument.CRASH: DrumNotationSpec(49, "A", 5, "x", "up"),
    DrumInstrument.RIDE: DrumNotationSpec(51, "F", 5, "x", "up"),
    DrumInstrument.RIDE_BELL: DrumNotationSpec(53, "F", 5, "diamond", "up"),
    DrumInstrument.COWBELL: DrumNotationSpec(56, "E", 5, "triangle", "up"),
}


def get_drum_spec(instrument: DrumInstrument) -> DrumNotationSpec:
    """Reject unresolved labels instead of silently assigning a different drum."""
    try:
        return _SPECS[instrument]
    except KeyError as exc:
        raise NotationExportError(f"No notation mapping for instrument: {instrument}") from exc