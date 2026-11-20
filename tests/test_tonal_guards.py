from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from make_demo import demo_score
from scoretool import ScoreError, key_interval, parse_key, transpose_score


def rest_only_piano(score):
    piano = score.find("./part[@id='P2']")
    for measure in piano.findall("measure"):
        for child in list(measure):
            if child.tag in ("note", "backup"):
                measure.remove(child)
        barline = measure.find("barline")
        rest = ET.fromstring('<note><rest measure="yes"/><duration>16</duration></note>')
        measure.insert(list(measure).index(barline) if barline is not None else len(measure), rest)
    return piano


class TonalGuards(unittest.TestCase):
    def test_target_guard_suggests_enharmonic_key_in_same_mode(self):
        for source, target, suggestion in (
            ("C", "B#", "C major"), ("C", "Fb", "E major"),
            ("Cm", "Dbm", "C# minor"), ("Cm", "B#m", "C minor"),
        ):
            with self.subTest(target=target), self.assertRaises(ScoreError) as caught:
                key_interval(parse_key(source), parse_key(target))
            self.assertIn(suggestion, str(caught.exception))
        self.assertEqual(key_interval(parse_key("B#"), parse_key("C")), (0, 1))

    def test_seven_sharps_and_flats_are_supported(self):
        for target in ("C#", "Cb", "A#m", "Abm"):
            with self.subTest(target=target):
                k = parse_key(target)
                self.assertEqual(abs(k.fifths), 7)
                self.assertEqual(key_interval(k, k), (0, 0))

    def test_later_modulation_checked_even_with_mismatch_override(self):
        score = demo_score()
        measure = score.find("./part[@id='P2']/measure[@number='3']")
        attributes = ET.fromstring("<attributes><key><fifths>6</fifths><mode>major</mode></key></attributes>")
        measure.insert(1, attributes)
        before = ET.tostring(score)
        with self.assertRaises(ScoreError) as caught:
            transpose_score(score, parse_key("Bb"), parse_key("C"), allow_key_mismatch=True)
        for fragment in ("P2", "measure 3", "+8", "Ab major"):
            self.assertIn(fragment, str(caught.exception))
        self.assertEqual(ET.tostring(score), before)

    def test_tacet_piano_updates_its_key_and_chord_without_adding_notes(self):
        score = demo_score()
        piano = rest_only_piano(score)
        first = piano.find("measure")
        first.insert(list(first).index(first.find("note")), ET.fromstring(
            "<harmony><root><root-step>B</root-step><root-alter>-1</root-alter></root>"
            "<kind>major</kind><bass><bass-step>F</bass-step></bass></harmony>"))
        moved, report = transpose_score(score, parse_key("Bb"), parse_key("C"))
        result = moved.find("./part[@id='P2']")
        self.assertEqual(result.findtext(".//key/fifths"), "0")
        self.assertEqual(result.findtext(".//harmony/root/root-step"), "C")
        self.assertEqual(result.findtext(".//harmony/bass/bass-step"), "G")
        self.assertEqual(len(result.findall(".//note/rest")), 4)
        self.assertEqual(result.findall(".//note/pitch"), [])
        self.assertEqual(report["transposed_notes"], 12)
        self.assertEqual(piano.findtext(".//key/fifths"), "-2")

    def test_tacet_staff_without_written_signature_does_not_invent_one(self):
        score = demo_score()
        attrs = rest_only_piano(score).find("./measure/attributes")
        attrs.remove(attrs.find("key"))
        moved, _ = transpose_score(score, parse_key("Bb"), parse_key("C"))
        self.assertIsNone(moved.find("./part[@id='P2']/measure/attributes/key"))

    def test_unpitched_only_staff_is_preserved_even_without_percussion_clef(self):
        score = demo_score()
        piano = rest_only_piano(score)
        note = piano.find("./measure/note")
        note.remove(note.find("rest"))
        note.insert(0, ET.fromstring("<unpitched><display-step>C</display-step><display-octave>4</display-octave></unpitched>"))
        moved, report = transpose_score(score, parse_key("Bb"), parse_key("C"))
        self.assertEqual(ET.tostring(moved.find("./part[@id='P2']")), ET.tostring(piano))
        self.assertIn("P2", report["skipped_parts"])

    def test_written_concert_mismatch_still_stops(self):
        score = demo_score()
        attrs = score.find("./part[@id='P2']/measure/attributes")
        attrs.find("key/fifths").text = "0"
        attrs.append(ET.fromstring("<transpose><diatonic>-1</diatonic><chromatic>-2</chromatic></transpose>"))
        with self.assertRaises(ScoreError) as caught:
            transpose_score(score, parse_key("Bb"), parse_key("C"))
        for fragment in ("P2", "written", "concert"):
            self.assertIn(fragment, str(caught.exception))
