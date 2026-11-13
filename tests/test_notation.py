"""Real MIDI/MusicXML round trips and isolated external-renderer checks."""

from pathlib import Path
import subprocess
from unittest.mock import patch
import xml.etree.ElementTree as ET

import mido
import pretty_midi
import pytest
from music21 import clef, converter, meter, stream

from engine.models.base import DrumInstrument
from engine.notation import NotationExportError
from engine.notation.drum_mapping import get_drum_spec
from engine.notation.midi import export_to_midi
from engine.notation.musicxml import export_to_musicxml
from engine.notation.pdf import render_musicxml_to_pdf
from engine.rhythm.schemas import BeatGrid, MusicalTranscription, QuantizedDrumEvent


def event(label: DrumInstrument, beat: float = 1, **overrides) -> QuantizedDrumEvent:
    values = dict(time_sec=(beat - 1) * 0.5 + 0.012, instrument=label,
                  midi_note=99, velocity=32, confidence=0.9, source="adtof",
                  onset_strength=5, is_ghost_note=label == DrumInstrument.SNARE,
                  measure=1, beat_in_measure=beat, grid_division="eighth",
                  quantized_time_sec=(beat - 1) * 0.5, micro_timing_ms=12,
                  duration_quarters=0.5)
    values.update(overrides)
    return QuantizedDrumEvent(**values)


@pytest.fixture
def transcription() -> MusicalTranscription:
    return MusicalTranscription(
        beat_grid=BeatGrid(bpm=120, beat_times_sec=[index * 0.5 for index in range(10)],
                           downbeat_times_sec=[0, 2, 4]),
        events=[event(DrumInstrument.KICK), event(DrumInstrument.CLOSED_HIHAT),
                event(DrumInstrument.SNARE, 2), event(DrumInstrument.CLOSED_HIHAT, 2)],
        total_measures=2,
    )


@pytest.mark.parametrize("quantized", [False, True])
def test_midi_round_trip(tmp_path: Path, transcription, quantized: bool) -> None:
    output = tmp_path / "exports" / "drums.mid"
    assert export_to_midi(transcription, output, quantized) == output
    parsed = pretty_midi.PrettyMIDI(str(output))
    assert len(parsed.instruments) == 1
    drums = parsed.instruments[0]
    assert drums.is_drum
    assert len(drums.notes) == 4
    assert sorted(hit.pitch for hit in drums.notes) == [36, 38, 42, 42]
    assert {hit.velocity for hit in drums.notes} == {32}
    assert drums.notes[0].start == pytest.approx(0 if quantized else 0.012, abs=0.0006)
    assert all(0.01 < hit.end - hit.start <= 0.151 for hit in drums.notes)
    assert parsed.get_tempo_changes()[1][0] == pytest.approx(120)
    assert parsed.time_signature_changes[0].numerator == 4
    messages = [message for track in mido.MidiFile(output).tracks for message in track
                if message.type == "note_on"]
    assert {message.channel for message in messages} == {9}


def test_all_resolved_instruments_have_specs() -> None:
    expected = [36, 38, 42, 44, 46, 45, 47, 50, 49, 51, 53, 56]
    assert [get_drum_spec(label).gm_midi_note for label in DrumInstrument
            if label != DrumInstrument.UNKNOWN] == expected
    with pytest.raises(NotationExportError, match="No notation mapping"):
        get_drum_spec(DrumInstrument.UNKNOWN)


def test_musicxml_round_trip(tmp_path: Path, transcription) -> None:
    output = tmp_path / "drums.musicxml"
    assert export_to_musicxml(transcription, output, "Practice") == output
    parsed = converter.parse(output)
    assert parsed.metadata.movementName == "Practice"
    assert len(parsed.recurse().getElementsByClass(clef.PercussionClef)) == 1
    assert parsed.recurse().getElementsByClass(meter.TimeSignature)[0].ratioString == "4/4"
    measures = list(parsed.parts[0].getElementsByClass(stream.Measure))
    assert [measure.number for measure in measures] == [1, 2]
    assert all(measure.highestTime == 4 for measure in measures)
    root = ET.parse(output).getroot()
    hits = root.findall(".//part/measure/note[unpitched]")
    assert len(hits) == 4
    assert {hit.findtext("voice") for hit in hits} == {"1", "2"}
    hats = [hit for hit in hits if hit.findtext("unpitched/display-step") == "G"]
    assert len(hats) == 2
    assert all(hit.findtext("notehead") == "x" for hit in hats)
    assert all(hit.findtext("stem") == "up" for hit in hats)
    assert any(hit.find("chord") is not None for hit in hits)
    assert root.find(".//notehead[@parentheses='yes']") is not None
    assert {entry.text for entry in root.findall(".//midi-unpitched")} >= {"37", "39", "43"}


def test_musicxml_triplets_and_eighth_denominator(tmp_path: Path, transcription) -> None:
    transcription.beat_grid = BeatGrid(
        bpm=120, time_signature_numerator=6, time_signature_denominator=8,
        beat_times_sec=[index * 0.25 for index in range(13)], downbeat_times_sec=[0, 1.5, 3],
    )
    transcription.events = [event(DrumInstrument.OPEN_HIHAT, 3, duration_quarters=1 / 3),
                            event(DrumInstrument.SNARE, 3 + 2 / 3, duration_quarters=1 / 3)]
    output = export_to_musicxml(transcription, tmp_path / "triplets.musicxml")
    parsed = converter.parse(output)
    notes = list(parsed.parts[0].recurse().notes)
    assert notes[0].getOffsetInHierarchy(parsed.parts[0]) == 1
    assert float(notes[1].getOffsetInHierarchy(parsed.parts[0])) == pytest.approx(4 / 3)
    assert float(notes[0].quarterLength) == pytest.approx(1 / 3)
    root = ET.parse(output).getroot()
    assert root.find(".//time-modification") is not None
    assert root.findtext(".//notehead") == "circle-x"


def test_musicxml_empty_bar_and_invalid_position(tmp_path: Path, transcription) -> None:
    transcription.events = []
    export_to_musicxml(transcription, tmp_path / "rests.musicxml")
    root = ET.parse(tmp_path / "rests.musicxml").getroot()
    assert len(root.findall(".//part/measure")) == 2
    assert root.find(".//rest") is not None
    transcription.events = [event(DrumInstrument.KICK, measure=3)]
    with pytest.raises(NotationExportError, match="outside"):
        export_to_musicxml(transcription, tmp_path / "invalid.musicxml")


def test_musicxml_all_heads_have_correct_gm_reference(tmp_path: Path, transcription) -> None:
    transcription.events = [event(label) for label in DrumInstrument
                            if label != DrumInstrument.UNKNOWN]
    output = export_to_musicxml(transcription, tmp_path / "kit.musicxml")
    root = ET.parse(output).getroot()
    instruments = {entry.attrib["id"]: int(entry.findtext("midi-unpitched")) - 1
                   for entry in root.findall(".//midi-instrument")}
    actual = {}
    for hit in root.findall(".//part/measure/note[unpitched]"):
        pitch = instruments[hit.find("instrument").attrib["id"]]
        actual[pitch] = (hit.findtext("unpitched/display-step"),
                         int(hit.findtext("unpitched/display-octave")),
                         hit.findtext("notehead", "normal"), hit.findtext("stem"))
    for hit in transcription.events:
        spec = get_drum_spec(hit.instrument)
        assert actual[spec.gm_midi_note] == (
            spec.staff_step, spec.staff_octave, spec.notehead, spec.stem_direction,
        )
    parsed = converter.parse(output)
    count = sum(len(hit.notes) if hasattr(hit, "notes") else 1
                for hit in parsed.parts[0].recurse().notes)
    assert count == len(transcription.events)


def test_musicxml_overlap_and_cross_bar_ties(tmp_path: Path, transcription) -> None:
    transcription.events = [event(DrumInstrument.SNARE, duration_quarters=1),
                            event(DrumInstrument.CLOSED_HIHAT, 1.5, duration_quarters=0.25),
                            event(DrumInstrument.CRASH, 4.5, duration_quarters=1)]
    output = export_to_musicxml(transcription, tmp_path / "ties.musicxml")
    root = ET.parse(output).getroot()
    durations = {}
    divisions = int(root.findtext(".//divisions"))
    for measure in root.findall("./part/measure"):
        for hit in measure.findall("note[unpitched]"):
            position = hit.findtext("unpitched/display-step")
            durations[position] = durations.get(position, 0) + int(hit.findtext("duration")) / divisions
    assert durations == {"C": 1, "G": 0.25, "A": 1}
    snare = [
        hit for hit in root.findall(".//part/measure/note[unpitched]")
        if hit.findtext("unpitched/display-step") == "C"
    ]
    assert [entry.attrib["type"] for hit in snare for entry in hit.findall("tie")] == [
        "start", "stop", "start", "stop",
    ]
    bars = root.findall("./part/measure")
    assert bars[0].find(".//tie[@type='start']") is not None
    assert bars[1].find(".//tie[@type='stop']") is not None


def test_musicxml_preserves_pickup_position(tmp_path: Path, transcription) -> None:
    transcription.beat_grid = BeatGrid(
        bpm=120, beat_times_sec=[-1.75 + index * 0.5 for index in range(13)],
        downbeat_times_sec=[-1.75, 0.25, 2.25, 4.25],
    )
    transcription.events = [event(DrumInstrument.SNARE, 4.5, quantized_time_sec=0)]
    output = export_to_musicxml(transcription, tmp_path / "pickup.musicxml")
    part = converter.parse(output).parts[0]
    assert part.recurse().notes[0].getOffsetInHierarchy(part) == 3.5


def test_midi_velocity_clamps_and_short_gates(tmp_path: Path, transcription) -> None:
    transcription.events = [event(DrumInstrument.PEDAL_HIHAT).model_copy(
        update={"velocity": 0, "duration_quarters": 0.001},
    ), event(DrumInstrument.RIDE_BELL, 2).model_copy(update={"velocity": 200})]
    output = export_to_midi(transcription, tmp_path / "clamped.mid")
    notes = pretty_midi.PrettyMIDI(str(output)).instruments[0].notes
    assert [(hit.pitch, hit.velocity) for hit in notes] == [(44, 1), (53, 127)]
    assert notes[0].end - notes[0].start > 0.01


@pytest.fixture
def xml_input(tmp_path: Path) -> Path:
    source = tmp_path / "drums.musicxml"
    source.write_text("<score-partwise/>", encoding="utf-8")
    return source


def successful_renderer(command, **kwargs):
    Path(command[command.index("-o") + 1]).write_bytes(b"%PDF-1.4\nmock renderer\n")
    return subprocess.CompletedProcess(command, 0, "", "")


def test_pdf_success_and_binary_fallback(tmp_path: Path, xml_input) -> None:
    output = tmp_path / "exports" / "drums.pdf"
    with patch("engine.notation.pdf.shutil.which", side_effect=[None, "mscore"]), \
            patch("engine.notation.pdf.subprocess.run", side_effect=successful_renderer) as run:
        assert render_musicxml_to_pdf(xml_input, output) == output
    assert output.read_bytes().startswith(b"%PDF-")
    command = run.call_args.args[0]
    assert command[0] == "mscore"
    assert command[-1] == str(xml_input.resolve())
    assert run.call_args.kwargs["env"]["QT_QPA_PLATFORM"] == "offscreen"
    assert run.call_args.kwargs["timeout"] == 60
    assert run.call_args.kwargs["capture_output"]


def test_pdf_missing_binary(tmp_path: Path, xml_input) -> None:
    with patch("engine.notation.pdf.shutil.which", return_value=None), \
            pytest.raises(NotationExportError, match="executable not found"):
        render_musicxml_to_pdf(xml_input, tmp_path / "score.pdf")


def test_pdf_invalid_input_and_timeout(tmp_path: Path, xml_input) -> None:
    with pytest.raises(NotationExportError, match="does not exist"):
        render_musicxml_to_pdf(tmp_path / "absent.musicxml", tmp_path / "score.pdf")
    with pytest.raises(NotationExportError, match="positive"):
        render_musicxml_to_pdf(xml_input, tmp_path / "score.pdf", timeout_sec=0)
    with pytest.raises(NotationExportError, match="must differ"):
        render_musicxml_to_pdf(xml_input, xml_input)


@pytest.mark.parametrize("contents,error", [(b"", "nonempty PDF"), (b"not a PDF", "invalid PDF")])
def test_pdf_invalid_output(tmp_path: Path, xml_input, contents, error) -> None:
    def renderer(command, **kwargs):
        Path(command[command.index("-o") + 1]).write_bytes(contents)
        return subprocess.CompletedProcess(command, 0, "", "")

    with patch("engine.notation.pdf.shutil.which", return_value="mscore3"), \
            patch("engine.notation.pdf.subprocess.run", side_effect=renderer), \
            pytest.raises(NotationExportError, match=error):
        render_musicxml_to_pdf(xml_input, tmp_path / "score.pdf")


def test_pdf_timeout_keeps_existing_output(tmp_path: Path, xml_input) -> None:
    output = tmp_path / "score.pdf"
    output.write_bytes(b"old export")
    with patch("engine.notation.pdf.shutil.which", return_value="mscore3"), \
            patch("engine.notation.pdf.subprocess.run",
                  side_effect=subprocess.TimeoutExpired("mscore3", 1)), \
            pytest.raises(NotationExportError, match="timed out"):
        render_musicxml_to_pdf(xml_input, output, timeout_sec=1)
    assert output.read_bytes() == b"old export"


@pytest.mark.parametrize("returncode,stderr,error", [(1, "bad score", "bad score"),
                                                    (0, "", "nonempty PDF")])
def test_pdf_failed_or_missing_output(tmp_path: Path, xml_input, returncode, stderr, error) -> None:
    output = tmp_path / "score.pdf"
    output.write_bytes(b"old export")
    with patch("engine.notation.pdf.shutil.which", return_value="mscore3"), \
            patch("engine.notation.pdf.subprocess.run",
                  return_value=subprocess.CompletedProcess([], returncode, "", stderr)), \
            pytest.raises(NotationExportError, match=error):
        render_musicxml_to_pdf(xml_input, output)
    assert output.read_bytes() == b"old export"


def test_pdf_optional_xvfb(tmp_path: Path, xml_input) -> None:
    def renderer(command, **kwargs):
        if command[0] == "xvfb-run":
            return successful_renderer(command, **kwargs)
        return subprocess.CompletedProcess(command, 1, "", "Qt platform unavailable")

    with patch("engine.notation.pdf.shutil.which", side_effect=["mscore3", "xvfb-run"]), \
            patch("engine.notation.pdf.subprocess.run", side_effect=renderer) as run:
        render_musicxml_to_pdf(xml_input, tmp_path / "score.pdf", use_xvfb_fallback=True)
    assert run.call_count == 2
    assert run.call_args.args[0][:2] == ["xvfb-run", "-a"]
    assert run.call_args.kwargs["env"]["QT_QPA_PLATFORM"] == "xcb"
    assert 0 < run.call_args.kwargs["timeout"] <= 60