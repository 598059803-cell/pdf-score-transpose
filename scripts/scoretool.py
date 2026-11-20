#!/usr/bin/env python3
"""Local PDF OMR, structural repair, and spelling-aware MusicXML transposition."""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import importlib.util
import json
from math import lcm
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from typing import Any
import uuid
import xml.etree.ElementTree as ET
import zipfile

VERSION = "0.1.1"
STEPS = "CDEFGAB"
NATURAL = (0, 2, 4, 5, 7, 9, 11)
MAJOR_FIFTHS = {"C": 0, "D": 2, "E": 4, "F": -1, "G": 1, "A": 3, "B": 5}
ACCIDENTALS = {-2: "flat-flat", -1: "flat", 0: "natural", 1: "sharp", 2: "double-sharp"}
XML_LIMIT = 64 * 1024 * 1024


class ScoreError(Exception):
    """An actionable input, configuration, or notation error."""


@dataclass(frozen=True)
class Key:
    step: str
    alter: int = 0
    mode: str = "major"

    @property
    def pc(self) -> int:
        return (NATURAL[STEPS.index(self.step)] + self.alter) % 12

    @property
    def fifths(self) -> int:
        return MAJOR_FIFTHS[self.step] + 7 * self.alter - (3 if self.mode == "minor" else 0)

    @property
    def label(self) -> str:
        return self.step + ("#" * self.alter if self.alter > 0 else "b" * -self.alter) + " " + self.mode


def parse_key(text: str) -> Key:
    value = text.strip().replace("♭", "b").replace("♯", "#")
    match = re.fullmatch(r"([A-Ga-g])([#b]{0,2})(?:\s*(major|minor|maj|min|m))?", value)
    if not match or ("#" in match[2] and "b" in match[2]):
        raise ScoreError(f"Invalid key {text!r}; use C, Bb, F#, Am, or 'Eb minor'.")
    mode = "minor" if match[3] in ("minor", "min", "m") else "major"
    return Key(match[1].upper(), match[2].count("#") - match[2].count("b"), mode)


def _check_output_signature(fifths: int, location: str, mode: str | None) -> None:
    """This engraver targets conventional signatures, even though MusicXML allows more."""
    if -7 <= fifths <= 7:
        return
    equivalent = min((f for f in range(-7, 8) if (f - fifths) % 12 == 0),
                     key=lambda f: (abs(f), (f < 0) != (fifths < 0)))
    spelling = f"{equivalent:+d} fifths"
    if mode in ("major", "minor"):
        keys = (Key(step, alteration, mode) for step in STEPS for alteration in (-1, 0, 1))
        spelling = next(key.label for key in keys if key.fifths == equivalent)
    raise ScoreError(f"{location} would have {fifths:+d} fifths (supported: -7..+7). "
                     f"Consider the enharmonic spelling {spelling} for this key and its notes.")


def key_interval(source: Key, target: Key, direction: str = "nearest") -> tuple[int, int]:
    if source.mode != target.mode:
        raise ScoreError("Changing major to minor is a reharmonization, not a uniform transposition.")
    _check_output_signature(target.fifths, f"Target {target.label}", target.mode)
    raw = target.pc - source.pc
    chromatic = raw % 12
    if direction == "down" and chromatic:
        chromatic -= 12
    elif direction == "nearest" and chromatic > 6:
        chromatic -= 12
    elif direction not in ("up", "down", "nearest"):
        raise ScoreError(f"Unknown direction: {direction}")
    # pc wraps accidentals. Account for tonic spellings such as Cb and B#.
    source_absolute = NATURAL[STEPS.index(source.step)] + source.alter
    target_absolute = NATURAL[STEPS.index(target.step)] + target.alter
    octave_adjustment = (chromatic - (target_absolute - source_absolute)) // 12
    diatonic = STEPS.index(target.step) - STEPS.index(source.step) + 7 * octave_adjustment
    return chromatic, diatonic


def _clean_xml(data: bytes) -> ET.Element:
    if len(data) > XML_LIMIT:
        raise ScoreError("MusicXML exceeds the 64 MiB input limit.")
    root = ET.fromstring(data)
    if root.tag.startswith("{"):
        namespace = root.tag.split("}", 1)[0] + "}"
        for e in root.iter():
            if e.tag.startswith(namespace):
                e.tag = e.tag[len(namespace):]
    if root.tag != "score-partwise":
        raise ScoreError("Expected score-partwise MusicXML; convert timewise/MEI files first.")
    return root


def load_score(path: str | Path) -> ET.Element:
    path = Path(path)
    if not path.is_file():
        raise ScoreError(f"Input does not exist: {path}")
    if path.suffix.lower() == ".sib":
        raise ScoreError("Native .sib must be opened in Sibelius; export MusicXML for this CLI.")
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            name = None
            if "META-INF/container.xml" in z.namelist():
                container = ET.fromstring(z.read("META-INF/container.xml"))
                roots = [e for e in container.iter() if e.tag.rsplit("}", 1)[-1] == "rootfile"]
                roots = [e for e in roots if e.get("media-type") == "application/vnd.recordare.musicxml+xml"] or roots
                if roots:
                    name = roots[0].get("full-path")
            if not name:
                names = [n for n in z.namelist() if n.lower().endswith((".xml", ".musicxml")) and not n.startswith("META-INF/")]
                if len(names) != 1:
                    raise ScoreError("MXL has no unambiguous score root in its container manifest.")
                name = names[0]
            if z.getinfo(name).file_size > XML_LIMIT:
                raise ScoreError("Uncompressed MusicXML exceeds the 64 MiB input limit.")
            return _clean_xml(z.read(name))
    return _clean_xml(path.read_bytes())


def xml_bytes(root: ET.Element) -> bytes:
    copy = deepcopy(root)
    ET.indent(copy, space="  ")
    return ET.tostring(copy, encoding="utf-8", xml_declaration=True)


def require_output(path: Path, overwrite: bool = False, source: Path | None = None) -> None:
    if source and path.resolve() == source.resolve():
        raise ScoreError("Choose a new output path; the source is preserved.")
    if path.exists() and not overwrite:
        raise ScoreError(f"Output exists: {path}. Choose another name or pass --overwrite.")
    path.parent.mkdir(parents=True, exist_ok=True)


def save_score(root: ET.Element, path: str | Path, overwrite: bool = False) -> None:
    path = Path(path)
    if path.suffix.lower() not in (".xml", ".musicxml", ".mxl"):
        raise ScoreError("MusicXML output must end in .musicxml, .xml, or .mxl; never rename it .sib.")
    require_output(path, overwrite)
    data = xml_bytes(root)
    if path.suffix.lower() == ".mxl":
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container><rootfiles>'
                '<rootfile full-path="score.musicxml" media-type="application/vnd.recordare.musicxml+xml"/>'
                '</rootfiles></container>')
            z.writestr("score.musicxml", data)
    else:
        path.write_bytes(data)


def write_json(path: Path, value: Any, overwrite: bool = False) -> None:
    require_output(path, overwrite)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def is_percussion(root: ET.Element, part: ET.Element) -> bool:
    definition = root.find(f'./part-list/score-part[@id="{part.get("id")}"]')
    return any(c.text == "percussion" for c in part.findall(".//clef/sign")) or (
        definition is not None and any(c.text == "10" for c in definition.findall(".//midi-channel")))


def _pitch_fields(pitch: ET.Element, location: str) -> tuple[int, int, Fraction]:
    step = (pitch.findtext("step") or "").strip()
    if len(step) != 1 or step not in STEPS:
        raise ScoreError(f"{location}: <pitch><step> must be one note name A-G; check the source MusicXML.")
    try:
        octave = int(pitch.findtext("octave") or "")
    except ValueError as exc:
        raise ScoreError(f"{location}: <pitch><octave> must contain an integer; check the source MusicXML.") from exc
    try:
        alter = Fraction(pitch.findtext("alter", "0"))
    except (ValueError, ZeroDivisionError) as exc:
        raise ScoreError(f"{location}: <pitch><alter> must contain a number of semitones; check the source MusicXML.") from exc
    return STEPS.index(step), octave, alter


def pitch_value(pitch: ET.Element, location: str = "MusicXML pitch") -> Fraction:
    degree, octave, alter = _pitch_fields(pitch, location)
    return (octave + 1) * 12 + NATURAL[degree] + alter


def _move_pitch(pitch: ET.Element, semitones: int, steps: int, location: str = "MusicXML pitch") -> None:
    old_degree, old_octave, old_alteration = _pitch_fields(pitch, location)
    old = (old_octave + 1) * 12 + NATURAL[old_degree] + old_alteration
    absolute_step = old_octave * 7 + old_degree + steps
    octave, degree = divmod(absolute_step, 7)
    alter = old + semitones - ((octave + 1) * 12 + NATURAL[degree])
    if alter.denominator != 1 or abs(alter) > 2:
        raise ScoreError(f"{location}: microtones or accidentals beyond double sharps/flats need a notation-aware manual pass.")
    pitch.find("step").text = STEPS[degree]
    pitch.find("octave").text = str(octave)
    old_alter = pitch.find("alter")
    if old_alter is not None:
        pitch.remove(old_alter)
    if alter:
        element = ET.Element("alter")
        element.text = str(alter.numerator)
        pitch.insert(1, element)
    if pitch_value(pitch) != old + semitones:
        raise ScoreError("Internal pitch verification failed.")


def transpose_score(root: ET.Element, source: Key, target: Key, direction: str = "nearest",
                    skip_parts: tuple[str, ...] = (), allow_key_mismatch: bool = False) -> tuple[ET.Element, dict]:
    semitones, steps = key_interval(source, target, direction)
    out = deepcopy(root)
    warnings = []
    count = 0
    skipped = []
    for part in out.findall("part"):
        if part.get("id") in skip_parts or is_percussion(out, part):
            skipped.append(part.get("id"))
            continue
        has_pitches = part.find(".//note/pitch") is not None
        if not has_pitches and part.find(".//note/unpitched") is not None:
            skipped.append(part.get("id"))
            continue
        if any(c.text == "TAB" for c in part.findall(".//clef/sign")) or part.find(".//technical/fret") is not None:
            raise ScoreError("Tablature/fretboard positions cannot be updated by this staff-notation transposer.")
        if part.find(".//frame") is not None:
            raise ScoreError("Chord-frame fingerings need manual transposition; remove or correct them explicitly first.")
        initial_signature = part.find("./measure/attributes/key")
        first_key = initial_signature.find("fifths") if initial_signature is not None else None
        first_mode = initial_signature.find("mode") if initial_signature is not None else None
        if first_mode is not None and first_mode.text in ("major", "minor") and first_mode.text != source.mode:
            raise ScoreError(f"Part {part.get('id')} declares {first_mode.text}, inconsistent with {source.label}.")
        if (first_key is None and has_pitches) or (first_key is not None and int(first_key.text) != source.fifths):
            message = (f"Part {part.get('id')} initial key does not match {source.label}; verify the written key "
                       "and notes against the score. For a transposing instrument, its written key may differ "
                       "from the concert key.")
            if not allow_key_mismatch:
                raise ScoreError(message)
            warnings.append(message)
        for measure in part.findall("measure"):
            for key in measure.findall("./attributes/key"):
                if key.find("key-step") is not None:
                    raise ScoreError("Nontraditional key signatures need a manual notation pass.")
                fifths = key.find("fifths")
                if fifths is not None:
                    value = int(fifths.text) + target.fifths - source.fifths
                    _check_output_signature(value, f"Part {part.get('id')}, measure {measure.get('number')}",
                                            key.findtext("mode"))
                    fifths.text = str(value)
                cancel = key.find("cancel")
                if cancel is not None:
                    key.remove(cancel)  # Let the destination engraver compute cancellation signs.
            location = f"Part {part.get('id', '?')}, measure {measure.get('number', '?')}"
            for note in measure.findall("note"):
                pitch = note.find("pitch")
                if pitch is None:
                    continue
                _move_pitch(pitch, semitones, steps, location)
                alter = int(pitch.findtext("alter", "0"))
                for accidental in note.findall("accidental"):
                    accidental.text = ACCIDENTALS[alter]
                for element in [note, *note.findall("stem")]:
                    if "default-y" in element.attrib:
                        element.set("default-y", str(float(element.get("default-y")) + steps * 5))
                count += 1
        for harmony in part.findall(".//harmony"):
            for tag in ("root", "bass"):
                element = harmony.find(tag)
                if element is None:
                    continue
                step = element.find(f"{tag}-step")
                if step is None:
                    continue
                temp = ET.Element("pitch")
                ET.SubElement(temp, "step").text = step.text
                ET.SubElement(temp, "alter").text = element.findtext(f"{tag}-alter", "0")
                ET.SubElement(temp, "octave").text = "4"
                _move_pitch(temp, semitones, steps)
                step.text = temp.findtext("step")
                old = element.find(f"{tag}-alter")
                if old is not None:
                    element.remove(old)
                if temp.find("alter") is not None:
                    new = ET.Element(f"{tag}-alter")
                    new.text = temp.findtext("alter")
                    element.insert(list(element).index(step) + 1, new)
    if not count:
        raise ScoreError("No pitched notes were transposed.")
    return out, {"from_key": source.label, "to_key": target.label, "semitones": semitones,
        "diatonic_steps": steps, "transposed_notes": count, "skipped_parts": skipped,
        "warnings": warnings, "status": "transposed_needs_proofreading"}


def inspect_score(root: ET.Element) -> dict:
    definitions = {p.get("id"): p.findtext("part-name", "") for p in root.findall("./part-list/score-part")}
    parts, rhythm = [], []
    for part in root.findall("part"):
        division, expected = Fraction(1), Fraction(4)
        pitches = []
        for measure in part.findall("measure"):
            current, end, previous_onset = Fraction(0), Fraction(0), Fraction(0)
            location = f"Part {part.get('id', '?')}, measure {measure.get('number', '?')}"
            for event in measure:
                if event.tag == "attributes":
                    if event.find("divisions") is not None:
                        division = Fraction(event.findtext("divisions"))
                    time = event.find("time")
                    if time is not None and time.find("beats") is not None:
                        beats = sum(Fraction(b) for b in time.findtext("beats").split("+"))
                        expected = beats * 4 / Fraction(time.findtext("beat-type"))
                duration = Fraction(event.findtext("duration", "0")) / division
                if event.tag == "backup":
                    current -= duration
                elif event.tag == "forward":
                    current += duration
                elif event.tag == "note":
                    pitch = event.find("pitch")
                    if pitch is not None:
                        pitches.append(pitch_value(pitch, location))
                    if event.find("chord") is None:
                        previous_onset = current
                        current += duration
                    end = max(end, previous_onset + duration)
                end = max(end, current)
            if end != expected and measure.get("implicit") != "yes":
                rhythm.append({"part": part.get("id"), "measure": measure.get("number"),
                    "beats": str(end), "expected": str(expected), "kind": "overfull" if end > expected else "underfull"})
        parts.append({"id": part.get("id"), "name": definitions.get(part.get("id"), ""),
            "measures": len(part.findall("measure")), "pitched_notes": len(pitches),
            "unpitched_notes": len(part.findall(".//note/unpitched")),
            "lyrics": len(part.findall(".//lyric/text")),
            "key_fifths": list(dict.fromkeys(k.text for k in part.findall(".//key/fifths"))),
            "midi_range": [str(min(pitches)), str(max(pitches))] if pitches else None,
            "percussion": is_percussion(root, part)})
    return {"title": root.findtext("./work/work-title", root.findtext("movement-title", "")),
        "parts": parts, "rhythm_findings": rhythm}


def repair_parts(root: ET.Element, omr_path: Path, names: list[str] | None = None) -> tuple[ET.Element, dict]:
    """Join logical aliases only when every physical system has the same staff structure."""
    source_parts = {p.get("id"): p for p in root.findall("part")}
    definitions = {p.get("id"): p for p in root.findall("./part-list/score-part")}
    systems = []
    pattern = None
    with zipfile.ZipFile(omr_path) as z:
        book = ET.fromstring(z.read("book.xml"))
        for sheet_ref in book.findall("sheet"):
            n = int(sheet_ref.get("number"))
            sheet = ET.fromstring(z.read(f"sheet#{n}/sheet#{n}.xml"))
            for page_ref in sheet_ref.findall("page"):
                page = sheet.find(f'page[@id="{page_ref.get("id")}"]')
                physical_systems = page.findall("system") if page is not None else []
                refs = page_ref.findall("system")
                if len(refs) != len(physical_systems):
                    raise ScoreError("OMR page/system references are inconsistent; repair parts manually.")
                for reference, physical in zip(refs, physical_systems):
                    staff_pattern = [len(p.findall("staff-configuration")) for p in reference.findall("part")]
                    if pattern is None:
                        pattern = staff_pattern
                    if staff_pattern != pattern or not all(pattern):
                        raise ScoreError("Physical staff structure changes (possibly hidden staves); automatic alias repair stopped.")
                    ids = [f"P{p.get('logical-id')}" for p in reference.findall("part")]
                    if any(pid not in source_parts for pid in ids):
                        raise ScoreError("OMR logical IDs do not match the exported MusicXML.")
                    stacks = physical.findall("stack")
                    if not stacks or any(s.get("multi-rest-count") for s in stacks):
                        raise ScoreError("Ambiguous/multirest measure mapping; review this book manually.")
                    systems.append((len(stacks), ids))
    if not systems:
        raise ScoreError("No physical systems found in the OMR project.")
    count = sum(n for n, _ in systems)
    if any(len(p.findall("measure")) != count for p in source_parts.values()):
        raise ScoreError("OMR stack count differs from MusicXML measures; refusing to guess bar alignment.")
    if names and len(names) != len(pattern):
        raise ScoreError(f"Expected {len(pattern)} part names, got {len(names)}.")
    original_divisions = {}
    divisions = []
    for pid, part in source_parts.items():
        current = 1
        inherited = []
        for m in part.findall("measure"):
            current = int(m.findtext("./attributes/divisions", str(current)))
            inherited.append(current)
            divisions.append(current)
        original_divisions[pid] = inherited
    common_division = lcm(*divisions)
    out = deepcopy(root)
    old_index = list(out).index(out.find("part-list"))
    for e in list(out):
        if e.tag in ("part-list", "part"):
            out.remove(e)
    listing = ET.Element("part-list")
    out.insert(old_index, listing)
    targets = []
    for position in range(len(pattern)):
        aliases = list(dict.fromkeys(ids[position] for _, ids in systems))
        definition = deepcopy(definitions[aliases[0]])
        definition.set("id", f"P{position+1}")
        if names:
            name = definition.find("part-name")
            if name is None:
                name = ET.SubElement(definition, "part-name")
            name.text = names[position]
        for tag in ("score-instrument", "midi-instrument"):
            for e in list(definition.findall(tag)):
                definition.remove(e)
            seen = set()
            for alias in aliases:
                for e in definitions[alias].findall(tag):
                    if e.get("id") not in seen:
                        definition.append(deepcopy(e))
                        seen.add(e.get("id"))
        # Instrument IDs remain globally unique and note references stay intact.
        listing.append(definition)
        targets.append(ET.SubElement(out, "part", {"id": f"P{position+1}"}))
    offset = 0
    mapping = []
    for number, (length, ids) in enumerate(systems, 1):
        mapping.append({"system": number, "bar_positions": [offset+1, offset+length], "source_parts": ids})
        for position, pid in enumerate(ids):
            for i in range(offset, offset+length):
                m = deepcopy(source_parts[pid].findall("measure")[i])
                div = original_divisions[pid][i]
                for value in m.findall(".//duration") + m.findall(".//offset"):
                    scaled = Fraction(value.text) * common_division / div
                    if scaled.denominator != 1:
                        raise ScoreError("Fractional division scaling needs a manual normalization pass.")
                    value.text = str(scaled.numerator)
                for value in m.findall("./attributes/divisions"):
                    value.text = str(common_division)
                if i == 0 and m.find("./attributes/divisions") is None:
                    attrs = m.find("attributes")
                    if attrs is None:
                        attrs = ET.Element("attributes")
                        m.insert(0, attrs)
                    e = ET.Element("divisions")
                    e.text = str(common_division)
                    attrs.insert(0, e)
                targets[position].append(m)
        offset += length
    return out, {"physical_staff_pattern": pattern, "before_parts": len(source_parts),
        "after_parts": len(targets), "measure_count": count, "mapping": mapping}


def load_config(path: str | None) -> dict:
    candidate = path or os.environ.get("SCOREFLOW_CONFIG")
    if candidate is None:
        for base in (Path.cwd(), Path(__file__).resolve().parent.parent):
            if (base/"config.local.toml").is_file():
                candidate = str(base/"config.local.toml")
                break
    if candidate:
        config_path = Path(candidate).expanduser().resolve()
        try:
            config = tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
        except UnicodeDecodeError as exc:
            raise ScoreError(f"Cannot read {config_path}: save the TOML configuration as UTF-8 (with or without BOM).") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ScoreError(f"Invalid TOML configuration in {config_path}: {exc}") from exc
        config["_base"] = str(config_path.parent)
        return config
    return {"tools": {}, "_base": str(Path.cwd())}


def tool_command(name: str, config: dict) -> list[str]:
    tools = config.get("tools", {})
    if name == "audiveris" and tools.get("audiveris_home"):
        home = Path(os.path.expandvars(tools["audiveris_home"])).expanduser()
        if not home.is_absolute():
            home = Path(config["_base"]) / home
        java = home / "runtime/bin" / ("java.exe" if os.name == "nt" else "java")
        if not java.is_file() or not (home/"app/audiveris.jar").is_file():
            raise ScoreError("audiveris_home must contain runtime/bin/java and app/audiveris.jar.")
        return [str(java), "-Xmx4G", "-Dfile.encoding=UTF-8", "--add-exports=java.desktop/sun.awt.image=ALL-UNNAMED",
            "--enable-native-access=ALL-UNNAMED", "-cp", str(home/"app/*"), "Audiveris"]
    value = tools.get(name, {"pdftoppm": "pdftoppm", "audiveris": "Audiveris", "musescore": "MuseScore4"}[name])
    command = [value] if isinstance(value, str) else list(value)
    if not command or not all(isinstance(x, str) for x in command):
        raise ScoreError(f"Invalid tools.{name}: expected executable or argument array.")
    executable = os.path.expandvars(command[0])
    if ("/" in executable or "\\" in executable) and not Path(executable).is_absolute():
        executable = str(Path(config["_base"])/executable)
    resolved = shutil.which(executable)
    if not resolved:
        raise ScoreError(f"Missing {name}: configure tools.{name} or put its executable on PATH.")
    if Path(resolved).suffix.lower() in (".bat", ".cmd"):
        raise ScoreError("Use an executable or audiveris_home instead of a shell/batch launcher.")
    return [resolved, *command[1:]]


def external(command: list[str], log: Path, timeout: int = 1800) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as stream:
        try:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT,
                timeout=timeout, check=False, shell=False)
        except subprocess.TimeoutExpired as exc:
            raise ScoreError(f"Tool timed out after {timeout}s; see {log}.") from exc
    if result.returncode:
        tail = "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-15:])
        raise ScoreError(f"Tool exited {result.returncode}; log: {log}\n{tail}")


def _validate_pixels(pixels: int) -> None:
    if not isinstance(pixels, int) or not 1000 <= pixels <= 10000:
        raise ScoreError("Choose --pixels between 1000 and 10000 (longest side).")


def prepare_pdf(source: Path, work: Path, config: dict, threshold: int | None = None,
                pixels: int = 3500, overwrite: bool = False) -> Path:
    _validate_pixels(pixels)
    try:
        from PIL import Image
    except ImportError as exc:
        raise ScoreError("PDF preparation needs Pillow; install this project with pip.") from exc
    if not source.is_file():
        raise ScoreError(f"PDF does not exist: {source}")
    output = work / "pages.tif"
    require_output(output, overwrite, source)
    raster = work / ("raster-" + uuid.uuid4().hex[:8])
    raster.mkdir(parents=True)
    external([*tool_command("pdftoppm", config), "-gray", "-scale-to", str(pixels), "-png",
        str(source.resolve()), str((raster/"page").resolve())], work/"prepare.log", timeout=600)
    files = sorted(raster.glob("page-*.png"), key=lambda p: int(p.stem.rsplit("-",1)[1]))
    if not files:
        raise ScoreError("Poppler did not produce any pages. Check whether an opening password is required.")
    pages = []
    for file in files:
        with Image.open(file) as raw:
            page = raw.convert("L")
        if threshold is not None:
            page = page.point(lambda v: 0 if v < threshold else 255, mode="1")
        pages.append(page)
    compression = "group4" if threshold is not None else "tiff_lzw"
    pages[0].save(output, save_all=True, append_images=pages[1:], compression=compression, dpi=(300,300))
    write_json(work/"prepare-report.json", {"status":"prepared", "source":str(source.resolve()),
        "source_sha256":hashlib.sha256(source.read_bytes()).hexdigest(), "page_count":len(pages),
        "preview_pages": [str(p.resolve()) for p in files], "threshold":threshold}, overwrite)
    return output


def recognize(image: Path, work: Path, config: dict, languages: str = "eng") -> tuple[Path, Path]:
    if not re.fullmatch(r"[a-zA-Z0-9_]+(?:\+[a-zA-Z0-9_]+)*", languages):
        raise ScoreError("Use Tesseract language codes, e.g. eng or chi_sim+eng.")
    work.mkdir(parents=True, exist_ok=True)
    if list(work.glob("*.mxl")) or list(work.glob("*.omr")):
        raise ScoreError("Use an empty OMR output directory; existing recognition projects are preserved.")
    external([*tool_command("audiveris", config), "-batch", "-transcribe", "-save", "-export",
        "-constant", f"org.audiveris.omr.text.Language.defaultSpecification={languages}",
        "-output", str(work.resolve()), "--", str(image.resolve())], work/"audiveris.log")
    exports, projects = list(work.rglob("*.mxl")), list(work.rglob("*.omr"))
    if len(exports) != 1 or len(projects) != 1:
        raise ScoreError(f"Expected one score/project; found {len(exports)} MXL and {len(projects)} OMR. Review multi-movement outputs.")
    return exports[0], projects[0]


def render_score(source: Path, output: Path, config: dict, overwrite: bool = False) -> None:
    if output.suffix.lower() not in (".pdf", ".png", ".mscz", ".mid"):
        raise ScoreError("MuseScore output must be .pdf, .png, .mscz, or .mid; .sib needs Sibelius.")
    require_output(output, overwrite, source)
    if output.suffix.lower() == ".png" and not overwrite and list(output.parent.glob(output.stem+"-*.png")):
        raise ScoreError("Rendered PNG pages already exist; choose another output prefix.")
    with tempfile.TemporaryDirectory(prefix="scoreflow-") as temporary:
        input_path = Path(temporary)/"input.mxl"
        save_score(load_score(source), input_path)
        external([*tool_command("musescore", config), str(input_path), "-f", "-o", str(output.resolve())],
            output.with_suffix(output.suffix+".log"), timeout=300)
    exists = output.is_file() or (output.suffix.lower()==".png" and bool(list(output.parent.glob(output.stem+"-*.png"))))
    if not exists:
        raise ScoreError("MuseScore exited without the requested output; see its log.")


def command_line() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="Tool-path TOML; otherwise SCOREFLOW_CONFIG, working-directory or skill-local config")
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Report configured tools; never install software")
    p = sub.add_parser("inspect", help="Inspect editable score structure and rhythm findings")
    p.add_argument("input", type=Path)
    p = sub.add_parser("prepare", help="Render a PDF to a normalized multipage TIFF")
    p.add_argument("input", type=Path)
    p.add_argument("--work-dir", required=True, type=Path)
    p.add_argument("--threshold", type=int, choices=range(1,256), metavar="1..255")
    p.add_argument("--pixels", type=int, default=3500)
    p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("recognize", help="Run configured Audiveris, saving its correction project")
    p.add_argument("input", type=Path)
    p.add_argument("--work-dir", required=True, type=Path)
    p.add_argument("--languages", default="eng")
    p = sub.add_parser("repair-parts", help="Join Audiveris logical aliases using a uniform physical staff structure")
    p.add_argument("input", type=Path)
    p.add_argument("--omr", required=True, type=Path)
    p.add_argument("--part-names", help="Comma-separated physical part names, top to bottom")
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("transpose", help="Transpose notes, key signatures, and chord roots together")
    p.add_argument("input", type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--skip-part", action="append", default=[])
    p.add_argument("--allow-key-mismatch", action="store_true", help="Only after source pitches/key were independently checked")
    p.add_argument("--overwrite", action="store_true")
    for option in ("from-key", "to-key"):
        p.add_argument("--"+option, required=True)
    p.add_argument("--direction", choices=("nearest","up","down"), default="nearest")
    p = sub.add_parser("render", help="Render editable notation using local MuseScore")
    p.add_argument("input", type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("verify-sib", help="Check a native Sibelius file signature (not musical correctness)")
    p.add_argument("input", type=Path)
    p = sub.add_parser("run", help="PDF -> OMR -> editable transposed score; Sibelius save is a separate native step")
    p.add_argument("input", type=Path)
    p.add_argument("--work-dir", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--from-key", required=True)
    p.add_argument("--to-key", required=True)
    p.add_argument("--direction", choices=("nearest","up","down"), default="nearest")
    p.add_argument("--languages", default="eng")
    p.add_argument("--threshold", type=int, choices=range(1,256), metavar="1..255")
    p.add_argument("--pixels", type=int, default=3500)
    p.add_argument("--part-names")
    p.add_argument("--no-repair-parts", action="store_true")
    p.add_argument("--render-pdf", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = command_line().parse_args(argv)
    try:
        if args.command in ("prepare", "run"):
            _validate_pixels(args.pixels)  # Before creating work/output directories.
        config = load_config(args.config)
        if args.command == "doctor":
            result = {"python":sys.version.split()[0], "pillow":bool(importlib.util.find_spec("PIL")), "tools":{}}
            for name in ("pdftoppm", "audiveris", "musescore"):
                try:
                    result["tools"][name] = {"available":True, "command":tool_command(name, config)}
                except ScoreError as exc:
                    result["tools"][name] = {"available":False, "reason":str(exc)}
        elif args.command == "inspect":
            result = inspect_score(load_score(args.input))
        elif args.command == "prepare":
            result = {"tiff":str(prepare_pdf(args.input,args.work_dir,config,args.threshold,args.pixels,args.overwrite))}
        elif args.command == "recognize":
            mxl, omr = recognize(args.input,args.work_dir,config,args.languages)
            result = {"musicxml":str(mxl),"omr":str(omr),"status":"recognized_needs_proofreading"}
        elif args.command == "repair-parts":
            require_output(args.output,args.overwrite,args.input)
            report = args.output.with_suffix(".parts-report.json")
            require_output(report,args.overwrite,args.input)
            root,result = repair_parts(load_score(args.input),args.omr,args.part_names.split(",") if args.part_names else None)
            save_score(root,args.output,args.overwrite)
            write_json(report,result,args.overwrite)
        elif args.command == "transpose":
            require_output(args.output,args.overwrite,args.input)
            report = args.output.with_suffix(".report.json")
            require_output(report,args.overwrite,args.input)
            root,result = transpose_score(load_score(args.input),parse_key(args.from_key),parse_key(args.to_key),
                args.direction,tuple(args.skip_part),args.allow_key_mismatch)
            result["score"] = inspect_score(root)
            save_score(root,args.output,args.overwrite)
            write_json(report,result,args.overwrite)
        elif args.command == "render":
            render_score(args.input,args.output,config,args.overwrite)
            result = {"output":str(args.output),"status":"rendered_needs_visual_review"}
        elif args.command == "verify-sib":
            data = args.input.read_bytes()
            if len(data)<32 or data[1:9] != b"SIBELIUS":
                raise ScoreError("This file does not have a native Sibelius signature.")
            result = {"native_signature":True,"bytes":len(data),"musical_correctness":"requires_native_open_and_proofreading"}
        elif args.command == "run":
            require_output(args.output,False,args.input)
            source, target = parse_key(args.from_key),parse_key(args.to_key)
            key_interval(source,target,args.direction)  # Fail before expensive recognition on invalid keys.
            require_output(args.work_dir/"run-report.json")
            pages = prepare_pdf(args.input,args.work_dir,config,args.threshold,args.pixels)
            mxl,omr = recognize(pages,args.work_dir/"omr",config,args.languages)
            root = load_score(mxl)
            repairs = None
            if not args.no_repair_parts:
                root,repairs = repair_parts(root,omr,args.part_names.split(",") if args.part_names else None)
            save_score(root,args.work_dir/"recognized.musicxml")
            moved,result = transpose_score(root,source,target,args.direction)
            save_score(moved,args.output)
            result.update({"source":str(args.input.resolve()),"output":str(args.output.resolve()),
                "recognition_project":str(omr.resolve()),"parts_repair":repairs,"score":inspect_score(moved)})
            if args.render_pdf:
                render_score(args.output,args.render_pdf,config)
                result["pdf"] = str(args.render_pdf.resolve())
            result["finished_utc"] = datetime.now(timezone.utc).isoformat()
            write_json(args.work_dir/"run-report.json",result)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (ScoreError, OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
        print(f"scoretool: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
