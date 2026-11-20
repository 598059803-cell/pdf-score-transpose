"""Optional local OMR acceptance test. No user score or commercial editor required."""
from collections import Counter
from fractions import Fraction
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from make_demo import demo_score
from scoretool import load_score, save_score


def musical_events(score, shift=0):
    """Compare musical time (not raw division units or XML chord ordering)."""
    events = Counter()
    naturals = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    for part_index, part in enumerate(score.findall("part")):
        divisions = 1
        for bar_index, measure in enumerate(part.findall("measure")):
            cursor = onset = Fraction(0)
            for item in measure:
                if item.tag == "attributes":
                    divisions = int(item.findtext("divisions", str(divisions)))
                elif item.tag in ("backup", "forward"):
                    amount = Fraction(int(item.findtext("duration")), divisions)
                    cursor += amount if item.tag == "forward" else -amount
                elif item.tag == "note":
                    duration = Fraction(int(item.findtext("duration")), divisions)
                    if item.find("chord") is None:
                        onset = cursor
                        cursor += duration
                    pitch = item.find("pitch")
                    if pitch is not None:
                        midi = (int(pitch.findtext("octave")) + 1) * 12 + naturals[pitch.findtext("step")]
                        midi += int(pitch.findtext("alter", "0"))
                        staff = int(item.findtext("staff", "1"))
                        events[part_index, bar_index, staff, onset, duration, midi + shift] += 1
    return events


@unittest.skipUnless(os.environ.get("SCOREFLOW_TEST_OMR") == "1",
                     "optional: requires configured Poppler, Audiveris and MuseScore")
class DemoPipelineTests(unittest.TestCase):
    def test_pdf_to_transposed_editable_score(self):
        requested = os.environ.get("SCOREFLOW_TEST_OUTPUT")
        if requested:
            folder = Path(requested).resolve()
            folder.mkdir(parents=True, exist_ok=False)
        else:
            scratch = tempfile.TemporaryDirectory()
            self.addCleanup(scratch.cleanup)
            folder = Path(scratch.name)

        def cli(*args):
            result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts/scoretool.py"),
                                     *map(str, args)], capture_output=True, text=True, encoding="utf-8",
                                    timeout=2400)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        source = demo_score()
        original, pdf = folder / "demo-Bb.mxl", folder / "demo-Bb.pdf"
        result, preview = folder / "demo-C.mxl", folder / "demo-C.pdf"
        save_score(source, original)
        cli("render", original, "--output", pdf)
        report = cli("run", pdf, "--work-dir", folder / "recognition", "--from-key", "Bb",
                     "--to-key", "C", "--direction", "up", "--output", result, "--render-pdf", preview)
        target = load_score(result)
        expected = musical_events(source, shift=2)
        self.assertEqual(sum(expected.values()), 32)
        self.assertEqual(musical_events(target), expected)
        self.assertEqual([p.findtext("part-name") for p in target.findall("./part-list/score-part")],
                         ["Soprano", "Piano"])
        self.assertEqual([len(p.findall("measure")) for p in target.findall("part")], [4, 4])
        for part in target.findall("part"):
            self.assertEqual({e.text for e in part.findall(".//key/fifths")}, {"0"})
        self.assertEqual(report["score"]["rhythm_findings"], [])
        self.assertEqual(report["status"], "transposed_needs_proofreading")
        self.assertTrue(preview.read_bytes().startswith(b"%PDF-"))
