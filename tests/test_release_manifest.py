from pathlib import Path
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from package_release import package


# An independent expectation: adding a public entry requires an intentional
# change to this review boundary, not just an edit to the packager.
EXPECTED = {
    ".github/workflows/tests.yml", ".gitignore", "LICENSE", "README.md", "SKILL.md",
    "agents/openai.yaml", "config.example.toml", "pyproject.toml",
    "references/omr-and-proofreading.md", "references/sibelius.md",
    "scripts/install_skill.py", "scripts/make_demo.py", "scripts/package_release.py",
    "scripts/scoretool.py", "tests/test_demo_pipeline.py", "tests/test_input_guards.py",
    "tests/test_release_manifest.py", "tests/test_scoretool.py", "tests/test_tonal_guards.py",
}


def fixture(root, omitted=()):
    for name in EXPECTED.difference(omitted):
        path = root / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_bytes(("public fixture: " + name).encode())


class ReleaseManifestTests(unittest.TestCase):
    def test_exact_public_manifest_and_no_incidental_files(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root = base / "checkout"
            fixture(root)
            for name in (
                "config.local.toml", "work/score.pdf", "outputs/score.sib",
                "scripts/private_transcription.py", "tests/test_unreviewed.py",
                "references/private-lyrics.md", "agents/private.yaml",
                ".github/workflows/unreviewed.yml",
            ):
                local = root / name
                local.parent.mkdir(exist_ok=True, parents=True)
                local.write_bytes(b"dummy private file - never package")
            target = base / "source.zip"
            listed = package(root, target)
            with zipfile.ZipFile(target) as archive:
                expected = ["pdf-score-transpose/" + name for name in sorted(EXPECTED)]
                self.assertEqual(sorted(archive.namelist()), expected)
                self.assertEqual(sorted(listed), expected)
                for name in EXPECTED:
                    self.assertEqual(archive.read("pdf-score-transpose/" + name),
                                     (root / name).read_bytes())

    def test_missing_required_file_aborts_without_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root = base / "checkout"
            fixture(root, {"scripts/scoretool.py"})
            output = base / "release.zip"
            with self.assertRaisesRegex(ValueError, "scripts/scoretool.py"):
                package(root, output)
            self.assertFalse(output.exists())

    def test_redirected_manifest_entry_is_not_published(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root = base / "checkout"
            fixture(root, {"agents/openai.yaml"})
            private = base / "private"
            private.mkdir()
            (private / "openai.yaml").write_bytes(b"private fixture")
            if os.name == "nt":
                # Unlike a Windows symlink, a local directory junction does not
                # require Developer Mode or an elevated user token.
                import _winapi
                _winapi.CreateJunction(str(private), str(root / "agents"))
            else:
                (root / "agents").symlink_to(private, target_is_directory=True)
            output = base / "release.zip"
            with self.assertRaisesRegex(ValueError, "redirected"):
                package(root, output)
            self.assertFalse(output.exists())
