from copy import deepcopy
from fractions import Fraction
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from make_demo import demo_score
from scoretool import (Key,ScoreError,parse_key,key_interval,pitch_value,transpose_score,
    inspect_score,repair_parts,save_score,load_score,main)
from package_release import public_files,package
from install_skill import install


def pitches(root):
    return [pitch_value(p) for p in root.findall(".//note/pitch")]


class TranspositionTests(unittest.TestCase):
    def test_all_standard_tonics_major_and_minor(self):
        for mode in ("major","minor"):
            # D-flat minor has eight flats; use the practical enharmonic spelling.
            keys=("C Db D Eb E F F# G Ab A Bb B" if mode=="major" else
                  "C C# D Eb E F F# G G# A Bb B").split()
            for first in keys:
                for last in keys:
                    a,b=parse_key(first+" "+mode),parse_key(last+" "+mode)
                    root=demo_score()
                    for k in root.findall(".//key/fifths"):
                        k.text=str(a.fifths)
                    for k in root.findall(".//key/mode"):
                        k.text=mode
                    before=xml(root)
                    for direction in ("nearest","up","down"):
                        moved,report=transpose_score(root,a,b,direction)
                        shift,_=key_interval(a,b,direction)
                        self.assertEqual([v+shift for v in pitches(root)],pitches(moved))
                        self.assertEqual({str(b.fifths)},{e.text for e in moved.findall(".//key/fifths")})
                        self.assertEqual(len(pitches(root)),report["transposed_notes"])
                    self.assertEqual(before,xml(root))

    def test_octave_boundary_spelling_and_retained_content(self):
        root=demo_score()
        n=root.find(".//note")
        ET.SubElement(n,"tie",{"type":"start"})
        lyric=ET.SubElement(n,"lyric",{"number":"1"})
        ET.SubElement(lyric,"text").text="示例"
        moved,_=transpose_score(root,parse_key("Bb"),parse_key("C"),"up")
        first=moved.find(".//note/pitch")
        self.assertEqual((first.findtext("step"),first.findtext("octave")),("C","5"))
        for tag in ("duration","type","voice","tie","lyric","backup"):
            self.assertEqual([xml(e) for e in root.findall(".//"+tag)],[xml(e) for e in moved.findall(".//"+tag)])
        self.assertEqual([xml(t) for t in root.findall(".//time")],[xml(t) for t in moved.findall(".//time")])

    def test_minor_and_enharmonic_interval(self):
        self.assertEqual(key_interval(parse_key("Am"),parse_key("Cm"),"up"),(3,2))
        self.assertEqual(key_interval(parse_key("Cb"),parse_key("C"),"up"),(1,0))
        self.assertEqual(key_interval(parse_key("B#"),parse_key("C")),(0,1))
        with self.assertRaises(ScoreError):
            key_interval(parse_key("A minor"),parse_key("C major"))

    def test_percussion_and_unpitched_are_unchanged(self):
        root=demo_score()
        drums=deepcopy(root.findall("part")[0])
        drums.set("id","P3")
        drums.find(".//clef/sign").text="percussion"
        root.append(drums)
        before=xml(drums)
        moved,report=transpose_score(root,parse_key("Bb"),parse_key("C"))
        self.assertEqual(before,xml(moved.findall("part")[-1]))
        self.assertEqual(["P3"],report["skipped_parts"])

    def test_harmony_roots_and_slash_bass(self):
        root=demo_score()
        h=ET.SubElement(root.find("./part/measure"),"harmony")
        r=ET.SubElement(h,"root")
        ET.SubElement(r,"root-step").text="B"
        ET.SubElement(r,"root-alter").text="-1"
        ET.SubElement(h,"kind").text="major"
        b=ET.SubElement(h,"bass")
        ET.SubElement(b,"bass-step").text="F"
        moved,_=transpose_score(root,parse_key("Bb"),parse_key("C"))
        self.assertEqual(moved.findtext(".//root-step"),"C")
        self.assertEqual(moved.findtext(".//bass-step"),"G")
        self.assertIsNone(moved.find(".//root-alter"))

    def test_key_mismatch_and_unsupported_notation_stop(self):
        with self.assertRaises(ScoreError):
            transpose_score(demo_score(),parse_key("G"),parse_key("C"))
        moved,report=transpose_score(demo_score(),parse_key("G"),parse_key("C"),allow_key_mismatch=True)
        self.assertTrue(report["warnings"])
        for tag in ("frame","key-step"):
            root=demo_score()
            if tag=="frame":
                ET.SubElement(ET.SubElement(root.find("./part/measure"),"harmony"),tag)
            else:
                ET.SubElement(root.find(".//key"),tag).text="F"
            with self.assertRaises(ScoreError):
                transpose_score(root,parse_key("Bb"),parse_key("C"))

    def test_rhythm_audit_handles_grand_staff_and_overflow(self):
        root=demo_score()
        self.assertEqual(inspect_score(root)["rhythm_findings"],[])
        root.find(".//note/duration").text="8"
        finding=inspect_score(root)["rhythm_findings"][0]
        self.assertEqual((finding["measure"],finding["kind"],finding["beats"]),("1","overfull","5"))


def xml(e):
    return ET.tostring(e,encoding="unicode")


class FilesAndRepairTests(unittest.TestCase):
    def test_mxl_manifest_and_overwrite_protection(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/"example.mxl"
            save_score(demo_score(),path)
            self.assertEqual(pitches(demo_score()),pitches(load_score(path)))
            with self.assertRaises(ScoreError):
                save_score(demo_score(),path)
            with self.assertRaises(ScoreError):
                save_score(demo_score(),Path(temporary)/"fake.sib")

    def test_cli_preserves_input_and_reports_real_shift(self):
        with tempfile.TemporaryDirectory() as temporary:
            source,target=Path(temporary)/"source.mxl",Path(temporary)/"target.mxl"
            save_score(demo_score(),source)
            before=source.read_bytes()
            args=["transpose",str(source),"--from-key","Bb","--to-key","C","--direction","up","--output",str(target)]
            with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                self.assertEqual(main(args),0)
                target_before=target.read_bytes()
                self.assertEqual(main(args),2)
            self.assertEqual(source.read_bytes(),before)
            self.assertEqual(target.read_bytes(),target_before)
            report=json.loads(target.with_suffix(".report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["semitones"],2)
            self.assertEqual(report["score"]["rhythm_findings"],[])

    def test_alias_repair_preserves_notes_and_normalizes_divisions(self):
        root,book,sheet=alias_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            omr=Path(temporary)/"example.omr"
            write_omr(omr,book,sheet)
            moved,report=repair_parts(root,omr,["Soprano","Piano"])
            self.assertEqual(report["before_parts"],3)
            self.assertEqual(report["after_parts"],2)
            self.assertEqual(report["physical_staff_pattern"],[1,2])
            first=moved.findall("part")[0]
            self.assertEqual([p.findtext("step") for p in first.findall(".//pitch")],["B","C"])
            self.assertEqual({"24"},{d.text for d in first.findall(".//attributes/divisions")})
            self.assertEqual(["96","96"],[d.text for d in first.findall(".//note/duration")])
            self.assertEqual(inspect_score(moved)["rhythm_findings"],[])

    def test_ambiguous_physical_structure_fails(self):
        root,book,sheet=alias_fixture()
        book.findall(".//system")[1].remove(book.findall(".//system")[1].findall("part")[1])
        with tempfile.TemporaryDirectory() as temporary:
            omr=Path(temporary)/"example.omr"
            write_omr(omr,book,sheet)
            with self.assertRaises(ScoreError):
                repair_parts(root,omr)

    def test_packaging_and_installer_exclude_score_data(self):
        repo=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/"source.zip"
            names=package(repo,output)
            self.assertTrue(any(n.endswith("SKILL.md") for n in names))
            self.assertFalse(any(n.endswith((".mxl",".sib",".pdf","config.local.toml")) for n in names))
            destination=Path(temporary)/"installed-skill"
            install(repo,destination)
            self.assertTrue((destination/"scripts/scoretool.py").is_file())
            self.assertFalse((destination/"tests").exists())
            with self.assertRaises(FileExistsError):
                install(repo,destination)


def alias_fixture():
    root=ET.Element("score-partwise",{"version":"3.1"})
    listing=ET.SubElement(root,"part-list")
    for number,div in ((1,4),(2,8),(3,12)):
        pid=f"P{number}"
        definition=ET.SubElement(listing,"score-part",{"id":pid})
        ET.SubElement(definition,"part-name").text=("Soprano","S","Piano")[number-1]
        part=ET.SubElement(root,"part",{"id":pid})
        for bar in (1,2):
            measure=ET.SubElement(part,"measure",{"number":str(bar)})
            attrs=ET.SubElement(measure,"attributes")
            ET.SubElement(attrs,"divisions").text=str(div)
            ET.SubElement(ET.SubElement(attrs,"key"),"fifths").text="-2"
            note=ET.SubElement(measure,"note")
            actual=(number==1 and bar==1) or (number==2 and bar==2) or number==3
            if actual:
                pitch=ET.SubElement(note,"pitch")
                ET.SubElement(pitch,"step").text="B" if bar==1 else "C"
                if bar==1:
                    ET.SubElement(pitch,"alter").text="-1"
                ET.SubElement(pitch,"octave").text="4"
            else:
                ET.SubElement(note,"rest",{"measure":"yes"})
            ET.SubElement(note,"duration").text=str(div*4)
            ET.SubElement(note,"type").text="whole"
    book=ET.Element("book")
    ref=ET.SubElement(ET.SubElement(book,"sheet",{"number":"1"}),"page",{"id":"1"})
    sheet=ET.Element("sheet")
    physical=ET.SubElement(sheet,"page",{"id":"1"})
    for logical in ("1","2"):
        system=ET.SubElement(ref,"system")
        for pid,count in ((logical,1),("3",2)):
            p=ET.SubElement(system,"part",{"logical-id":pid})
            for _ in range(count):
                ET.SubElement(p,"staff-configuration",{"line-count":"5"})
        ET.SubElement(ET.SubElement(physical,"system"),"stack",{"id":"1"})
    return root,book,sheet


def write_omr(path,book,sheet):
    with zipfile.ZipFile(path,"w") as z:
        z.writestr("book.xml",ET.tostring(book))
        z.writestr("sheet#1/sheet#1.xml",ET.tostring(sheet))


if __name__=="__main__":
    unittest.main()
