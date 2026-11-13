"""Quantized, two-voice unpitched drum engraving with music21."""

from collections import defaultdict
from fractions import Fraction
from pathlib import Path
import xml.etree.ElementTree as ET

from music21 import clef, exceptions21, instrument, metadata, meter, note, percussion, stream, tempo, tie

from engine.notation import NotationExportError
from engine.notation.drum_mapping import get_drum_spec
from engine.rhythm.schemas import MusicalTranscription, QuantizedDrumEvent


def _set_drum_instruments(output_path: Path, events: list[QuantizedDrumEvent]) -> None:
    """Music21 selects a chord-wide instrument; MusicXML needs per-head GM IDs."""
    document = ET.parse(output_path)
    root = document.getroot()
    score_part = root.find("./part-list/score-part")
    specs = {get_drum_spec(event.instrument): event.instrument.value for event in events}
    identifiers = {}
    for existing in list(score_part):
        if existing.tag in {"score-instrument", "midi-instrument"}:
            score_part.remove(existing)
    for spec, label in sorted(specs.items(), key=lambda entry: entry[0].gm_midi_note):
        identifier = f"{score_part.attrib['id']}-D{spec.gm_midi_note}"
        identifiers[spec.staff_step, spec.staff_octave, spec.notehead] = identifier
        entry = ET.SubElement(score_part, "score-instrument", id=identifier)
        ET.SubElement(entry, "instrument-name").text = label.replace("_", " ").title()
    for spec in sorted(specs, key=lambda spec: spec.gm_midi_note):
        identifier = identifiers[spec.staff_step, spec.staff_octave, spec.notehead]
        entry = ET.SubElement(score_part, "midi-instrument", id=identifier)
        ET.SubElement(entry, "midi-channel").text = "10"
        ET.SubElement(entry, "midi-unpitched").text = str(spec.gm_midi_note + 1)
    for hit in root.findall("./part/measure/note[unpitched]"):
        key = (hit.findtext("unpitched/display-step"),
               int(hit.findtext("unpitched/display-octave")), hit.findtext("notehead", "normal"))
        reference = ET.Element("instrument", id=identifiers[key])
        hit.insert(list(hit).index(hit.find("voice")), reference)
    ET.indent(document, space="  ")
    document.write(output_path, encoding="utf-8", xml_declaration=True)


def _fill_voice(
    voice: stream.Voice,
    spans: list[tuple[Fraction, Fraction, QuantizedDrumEvent]],
    bar_length: Fraction,
    direction: str,
) -> None:
    starts: dict[Fraction, list[int]] = defaultdict(list)
    stops: dict[Fraction, list[int]] = defaultdict(list)
    for index, (start, end, _) in enumerate(spans):
        starts[max(Fraction(0), start)].append(index)
        stops[min(bar_length, end)].append(index)
    boundaries = sorted({Fraction(0), bar_length, *starts, *stops})
    active: dict[int, tuple[Fraction, Fraction, QuantizedDrumEvent]] = {}
    for left, right in zip(boundaries, boundaries[1:]):
        for index in stops[left]:
            active.pop(index, None)
        for index in starts[left]:
            active[index] = spans[index]
        if not active:
            continue
        notes: list[note.Unpitched] = []
        for start, end, event in active.values():
            spec = get_drum_spec(event.instrument)
            hit = note.Unpitched()
            hit.displayStep = spec.staff_step
            hit.displayOctave = spec.staff_octave
            hit.notehead = spec.notehead
            hit.noteheadParenthesis = event.is_ghost_note
            hit.stemDirection = direction
            hit.quarterLength = right - left
            hit.volume.velocity = event.velocity
            if left > start or right < end:
                hit.tie = tie.Tie("continue" if left > start and right < end else
                                  "stop" if left > start else "start")
            notes.append(hit)
        sounding = notes[0] if len(notes) == 1 else percussion.PercussionChord(notes)
        sounding.quarterLength = right - left
        sounding.stemDirection = direction
        voice.insert(left, sounding)
    voice.makeRests(fillGaps=True, inPlace=True, refStreamOrTimeRange=[0, bar_length])


def export_to_musicxml(
    transcription: MusicalTranscription,
    output_path: Path,
    title: str = "Drum Transcription",
) -> Path:
    """Use measure/denominator-beat positions, never expressive onset seconds.

    Pickup material retains its existing measure number and leading rests.
    Overlapping durations are split and tied without adding new attacks.
    """
    if transcription.total_measures == 0:
        raise NotationExportError("MusicXML needs at least one measure")
    grid = transcription.beat_grid
    beat_length = Fraction(4, grid.time_signature_denominator)
    bar_length = grid.time_signature_numerator * beat_length
    grouped: dict[tuple[int, str], list] = defaultdict(list)
    for event in transcription.events:
        spec = get_drum_spec(event.instrument)
        offset = Fraction(event.beat_in_measure - 1).limit_denominator(960) * beat_length
        if event.measure > transcription.total_measures or not 0 <= offset < bar_length:
            raise NotationExportError("Event position is outside the declared measures")
        start = (event.measure - 1) * bar_length + offset
        end = start + Fraction(event.duration_quarters).limit_denominator(960)
        if end <= start or end > transcription.total_measures * bar_length:
            raise NotationExportError("Event duration extends outside the declared score")
        final_measure = min(transcription.total_measures, int(end // bar_length) + 1)
        for measure_number in range(event.measure, final_measure + 1):
            origin = (measure_number - 1) * bar_length
            if end > origin:
                grouped[measure_number, spec.stem_direction].append(
                    (start - origin, end - origin, event),
                )
    score = stream.Score()
    score.metadata = metadata.Metadata(title=title)
    part = stream.Part(id="Drums")
    part.partName = "Drums"
    part.insert(0, instrument.Percussion())
    score.insert(0, part)
    try:
        for measure_number in range(1, transcription.total_measures + 1):
            measure = stream.Measure(number=measure_number)
            if measure_number == 1:
                measure.insert(0, clef.PercussionClef())
                measure.insert(0, meter.TimeSignature(
                    f"{grid.time_signature_numerator}/{grid.time_signature_denominator}",
                ))
                measure.insert(0, tempo.MetronomeMark(number=grid.bpm))
            for voice_number, direction in ((1, "up"), (2, "down")):
                voice = stream.Voice(id=voice_number)
                _fill_voice(voice, grouped[measure_number, direction], bar_length, direction)
                measure.insert(0, voice)
            part.insert((measure_number - 1) * bar_length, measure)
        part.makeBeams(inPlace=True, setStemDirections=False)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        score.write("musicxml", fp=str(output_path))
        _set_drum_instruments(output_path, transcription.events)
    except (OSError, ValueError, exceptions21.Music21Exception) as exc:
        raise NotationExportError(f"Cannot write MusicXML to {output_path}: {exc}") from exc
    return output_path