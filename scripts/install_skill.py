#!/usr/bin/env python3
"""Install only runtime skill files; never overwrite an existing skill."""
import argparse
import os
from pathlib import Path
import shutil


def install(source: Path, destination: Path, config: Path | None = None):
    files=["SKILL.md","LICENSE","config.example.toml","scripts/scoretool.py"]
    files += [str(p.relative_to(source)) for p in (source/"agents").glob("*.yaml")]
    files += [str(p.relative_to(source)) for p in (source/"references").glob("*.md")]
    for name in files:
        if not (source/name).is_file():
            raise ValueError(f"Missing runtime file: {name}")
    if config is not None and not config.is_file():
        raise ValueError(f"Configuration does not exist: {config}")
    if destination.exists():
        raise FileExistsError(f"Skill already exists: {destination}; preserve it or choose --destination.")
    destination.mkdir(parents=True)
    for name in files:
        target=destination/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source/name,target)
    if config is not None:
        shutil.copy2(config,destination/"config.local.toml")
    return len(files)+(config is not None)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination",type=Path)
    parser.add_argument("--config",type=Path,help="Optional private tool-path config; not part of the public package")
    args=parser.parse_args()
    source=Path(__file__).resolve().parent.parent
    codex_base=Path(os.environ["CODEX_HOME"]) if os.environ.get("CODEX_HOME") else Path.home()/".codex"
    destination=args.destination or codex_base/"skills/pdf-score-transpose"
    count=install(source,destination,args.config)
    print(f"Installed {count} runtime files to {destination}")
