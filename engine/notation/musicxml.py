"""Quantized, two-voice unpitched drum engraving with music21."""

from collections import defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from music21 import clef, exceptions21, instrument, metadata, meter, note, percussion, stream, tempo, tie

from engine.notation import NotationExportError
from engine.notation.drum_mapping import get_drum_spec
from engine.rhythm.schemas import MusicalTranscription, QuantizedDrumEvent


def _insert_direction(measure: ET.Element, direction: ET.Element) -> None:
    children = list(measure)
    index = next(
        (position for position, child in enumerate(children)
         if child.tag in {"note", "backup", "forward", "barline"}),
        len(children),
    )
    measure.insert(index, direction)


def _direction(
    *, words: str | None = None, dynamic: str | None = None, offset: int | None = None,
) -> ET.Element:
    direction = ET.Element("direction", placement="above")
    direction_type = ET.SubElement(direction, "direction-type")
    if dynamic:
        dynamics = ET.SubElement(direction_type, "dynamics")
        ET.SubElement(dynamics, dynamic)
    if words is not None:
        ET.SubElement(direction_type, "words", attrib={"font-weight": "bold"}).text = words
    if offset is not None:
        ET.SubElement(direction, "offset").text = str(offset)
    ET.SubElement(direction, "staff").text = "1"
    return direction


def _set_repeat_barline(measure: ET.Element, location: str, direction: str) -> None:
    barline = next(
        (element for element in measure.findall("barline") if element.get("location") == location),
        None,
    )
    if barline is None:
        barline = ET.SubElement(measure, "barline", location=location)
    if barline.find("bar-style") is None:
        ET.SubElement(barline, "bar-style").text = "heavy-light" if direction == "forward" else "light-heavy"
    if barline.find(f"repeat[@direction='{direction}']") is None:
        ET.SubElement(barline, "repeat", direction=direction)


def _add_accents(
    document: ET.ElementTree, transcription: MusicalTranscription,
    measures: list[ET.Element], divisions: int, threshold: int,
) -> None:
    score_part = document.find("./part-list/score-part")
    if score_part is None:
        return
    part_id = score_part.attrib["id"]
    events_by_measure: dict[int, list[QuantizedDrumEvent]] = defaultdict(list)
    for event in transcription.events:
        events_by_measure[event.measure].append(event)

    for measure_number, measure in enumerate(measures, start=1):
        events = events_by_measure[measure_number]
        cursor = 0
        last_start = 0
        for element in list(measure):
            if element.tag == "backup":
                cursor -= int(element.findtext("duration") or 0)
                continue
            if element.tag == "forward":
                cursor += int(element.findtext("duration") or 0)
                continue
            if element.tag != "note":
                continue
            chord_member = element.find("chord") is not None
            offset = last_start if chord_member else cursor
            if not chord_member:
                last_start = cursor
            instrument = element.find("instrument")
            for event in events:
                if event.velocity < threshold:
                    continue
                expected_instrument = f"{part_id}-D{get_drum_spec(event.instrument).gm_midi_note}"
                target_offset = round(
                    (event.beat_in_measure - 1) * 4 / transcription.beat_grid.time_signature_denominator * divisions,
                )
                if instrument is None or instrument.get("id") != expected_instrument or offset != target_offset:
                    continue
                notations = element.find("notations")
                if notations is None:
                    notations = ET.Element("notations")
                    lyrics = element.find("lyric")
                    element.insert(list(element).index(lyrics) if lyrics is not None else len(element), notations)
                articulations = notations.find("articulations")
                if articulations is None:
                    articulations = ET.SubElement(notations, "articulations")
                if articulations.find("accent") is None:
                    ET.SubElement(articulations, "accent")
            if not chord_member:
                cursor += int(element.findtext("duration") or 0)


def _apply_notation_options(
    output_path: Path, transcription: MusicalTranscription, options: dict[str, Any],
) -> None:
    if not options:
        return
    document = ET.parse(output_path)
    measures = document.findall("./part/measure")
    if not measures:
        return
    denominator = transcription.beat_grid.time_signature_denominator
    divisions = int(document.findtext(".//attributes/divisions") or "1")
    events_by_measure: dict[int, list[QuantizedDrumEvent]] = defaultdict(list)
    for event in transcription.events:
        events_by_measure[event.measure].append(event)

    def at_event(event: QuantizedDrumEvent, words: str) -> ET.Element:
        quarter_offset = (event.beat_in_measure - 1) * 4 / denominator
        offset = max(0, round(quarter_offset * divisions))
        return _direction(words=words, offset=offset)

    measures_per_system = int(options.get("measures_per_system", 4))
    for index, measure in enumerate(measures):
        if index and index % measures_per_system == 0:
            print_element = ET.Element("print", {"new-system": "yes"})
            measure.insert(0, print_element)
        if options.get("show_dynamics") and events_by_measure[index + 1]:
            average_velocity = sum(event.velocity for event in events_by_measure[index + 1]) / len(events_by_measure[index + 1])
            dynamic = ("pp" if average_velocity < 25 else "p" if average_velocity < 40 else
                       "mp" if average_velocity < 55 else "mf" if average_velocity < 75 else
                       "f" if average_velocity < 100 else "ff")
            _insert_direction(measure, _direction(dynamic=dynamic))

    for section in options.get("sections", []):
        start = section["start_measure"]
        end = section["end_measure"]
        if start > len(measures) or end > len(measures):
            raise ValueError("Notation section measures must fit within the current score.")
        _insert_direction(measures[start - 1], _direction(words=section["label"]))
        if section.get("repeat"):
            _set_repeat_barline(measures[start - 1], "left", "forward")
            _set_repeat_barline(measures[end - 1], "right", "backward")

    if options.get("show_sticking"):
        onsets: dict[tuple[int, float], list[QuantizedDrumEvent]] = defaultdict(list)
        for event in transcription.events:
            onsets[event.measure, round(event.beat_in_measure, 6)].append(event)
        hand_index = 0
        for (measure_number, _), events in sorted(onsets.items()):
            ordered = sorted(events, key=lambda event: event.midi_note)
            stick = "R+L" if len(ordered) > 1 else "R" if hand_index % 2 == 0 else "L"
            event = ordered[0]
            _insert_direction(measures[measure_number - 1], at_event(event, stick))
            hand_index += max(1, len(ordered))

    if options.get("show_accents"):
        _add_accents(
            document, transcription, measures, divisions,
            int(options.get("accent_threshold", 100)),
        )

    ET.indent(document, space="  ")
    document.write(output_path, encoding="utf-8", xml_declaration=True)


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
    notation_options: dict[str, Any] | None = None,
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
        if notation_options:
            _apply_notation_options(output_path, transcription, notation_options)
    except (OSError, ValueError, exceptions21.Music21Exception) as exc:
        raise NotationExportError(f"Cannot write MusicXML to {output_path}: {exc}") from exc
    return output_path