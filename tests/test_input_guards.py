"""Public, synthetic regressions for v0.1.1 input handling."""

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree.ElementTree import SubElement

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from make_demo import demo_score
from scoretool import ScoreError, load_config, main, prepare_pdf, save_score


class InputGuardTests(unittest.TestCase):
    def test_incomplete_pitch_reports_measure_and_field_at_both_cli_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source, target = base / "source.musicxml", base / "out.mxl"
            config = base / "config.toml"
            config.write_text("[tools]\n", encoding="utf-8")
            for field, value in (("step", None), ("step", "CD"), ("octave", None),
                                 ("octave", "four"), ("alter", "invalid")):
                root = demo_score()
                pitch = root.find("./part[@id='P2']/measure[@number='2']/note/pitch")
                element = pitch.find(field)
                if value is None:
                    pitch.remove(element)
                else:
                    if element is None:
                        element = SubElement(pitch, field)
                    element.text = value
                save_score(root, source, overwrite=True)
                for command in (["inspect", str(source)], ["transpose", str(source),
                                "--from-key", "Bb", "--to-key", "C", "--output", str(target)]):
                    with self.subTest(field=field, value=value, command=command[0]):
                        err = io.StringIO()
                        with redirect_stderr(err), redirect_stdout(io.StringIO()):
                            self.assertEqual(main(["--config", str(config), *command]), 2)
                        self.assertIn("Part P2, measure 2", err.getvalue())
                        self.assertIn(f"<{field}>", err.getvalue())
                        self.assertNotIn("Traceback", err.getvalue())
                        self.assertFalse(target.exists())

    def test_config_accepts_utf8_with_and_without_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.toml"
            for encoding in ("utf-8", "utf-8-sig"):
                with self.subTest(encoding=encoding):
                    config.write_text('[tools]\npdftoppm = "工具/Poppler"\n', encoding=encoding)
                    loaded = load_config(str(config))
                    self.assertEqual(loaded["tools"]["pdftoppm"], "工具/Poppler")
                    self.assertEqual(loaded["_base"], str(config.resolve().parent))

    def test_bad_config_says_which_config_and_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.toml"
            for data, reason in ((b"[tools\n", "Invalid TOML"), (b"\xff\xfe\xff", "UTF-8")):
                config.write_bytes(data)
                with self.subTest(reason=reason), self.assertRaises(ScoreError) as error:
                    load_config(str(config))
                self.assertIn(reason, str(error.exception))
                self.assertIn(str(config.resolve()), str(error.exception))

    def test_prepare_and_run_share_pixel_bounds_before_tools_or_output(self):
        with tempfile.TemporaryDirectory() as tmp, patch("scoretool.external") as external:
            base = Path(tmp)
            source, work, target = base / "unused.pdf", base / "work", base / "out" / "result.mxl"
            for pixels in (999, 10001):
                for name in ("prepare", "run"):
                    args = [name, str(source), "--work-dir", str(work), "--pixels", str(pixels)]
                    if name == "run":
                        args += ["--output", str(target), "--from-key", "Bb", "--to-key", "C"]
                    err = io.StringIO()
                    with self.subTest(name=name, pixels=pixels), redirect_stderr(err):
                        self.assertEqual(main(args), 2)
                        self.assertIn("1000 and 10000", err.getvalue())
                with self.assertRaisesRegex(ScoreError, "--pixels"):
                    prepare_pdf(source, work, {}, pixels=pixels)
            external.assert_not_called()
            self.assertFalse(work.exists())
            self.assertFalse(target.parent.exists())
