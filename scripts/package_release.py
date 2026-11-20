#!/usr/bin/env python3
"""Build a source-only ZIP from an explicit allowlist, excluding local score data."""
import argparse
import hashlib
from pathlib import Path
import zipfile


# Entries are reviewed individually. Do not replace this manifest with globs:
# working directories may contain private scores, transcription scripts or notes.
PUBLIC_FILES = (
    ".github/workflows/tests.yml",
    ".gitignore",
    "LICENSE",
    "README.md",
    "SKILL.md",
    "agents/openai.yaml",
    "config.example.toml",
    "pyproject.toml",
    "references/omr-and-proofreading.md",
    "references/sibelius.md",
    "scripts/install_skill.py",
    "scripts/make_demo.py",
    "scripts/package_release.py",
    "scripts/scoretool.py",
    "tests/test_demo_pipeline.py",
    "tests/test_input_guards.py",
    "tests/test_release_manifest.py",
    "tests/test_scoretool.py",
    "tests/test_tonal_guards.py",
)


def public_files(root: Path) -> list[Path]:
    base = root.resolve(strict=True)
    paths = []
    for name in PUBLIC_FILES:
        path = root / name
        if not path.is_file():
            raise ValueError(f"Missing reviewed release file: {name}")
        # Resolving also detects directory junctions on Windows.
        if path.resolve(strict=True) != base / name:
            raise ValueError(f"Reviewed release file must not be redirected: {name}")
        paths.append(path)
    return paths


def package(root: Path,output: Path) -> list[str]:
    if output.exists():
        raise FileExistsError(f"Choose a new archive name: {output}")
    paths=public_files(root)
    output.parent.mkdir(parents=True,exist_ok=True)
    names=[]
    with zipfile.ZipFile(output,"x",zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            name="pdf-score-transpose/"+path.relative_to(root).as_posix()
            archive.write(path,name)
            names.append(name)
    return names


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",required=True,type=Path)
    args=parser.parse_args()
    names=package(Path(__file__).resolve().parent.parent,args.output)
    print("\n".join(names))
    print(f"{len(names)} source files; SHA256 {hashlib.sha256(args.output.read_bytes()).hexdigest()}")
