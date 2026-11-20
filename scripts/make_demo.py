#!/usr/bin/env python3
"""Generate a small original example; no third-party sheet music is bundled."""
import argparse
from pathlib import Path
import xml.etree.ElementTree as ET
from scoretool import save_score


def demo_score():
    root = ET.Element("score-partwise", {"version":"3.1"})
    ET.SubElement(ET.SubElement(root,"work"),"work-title").text="Original four-bar example"
    listing=ET.SubElement(root,"part-list")
    for pid,name in (("P1","Soprano"),("P2","Piano")):
        definition=ET.SubElement(listing,"score-part",{"id":pid})
        ET.SubElement(definition,"part-name").text=name
        ET.SubElement(definition,"part-abbreviation").text="S" if pid=="P1" else "Pno."
    melody=[[("B",-1,4,4),("D",0,5,4),("F",0,5,4),("B",-1,5,4)],
            [("A",0,5,4),("G",0,5,4),("F",0,5,8)],
            [("E",-1,5,4),("D",0,5,4),("C",0,5,4),("A",0,4,4)],
            [("B",-1,4,16)]]
    for pid in ("P1","P2"):
        part=ET.SubElement(root,"part",{"id":pid})
        for i in range(4):
            measure=ET.SubElement(part,"measure",{"number":str(i+1)})
            if i==2:
                ET.SubElement(measure,"print",{"new-system":"yes"})
            if i==0:
                attrs=ET.SubElement(measure,"attributes")
                ET.SubElement(attrs,"divisions").text="4"
                key=ET.SubElement(attrs,"key")
                ET.SubElement(key,"fifths").text="-2"
                ET.SubElement(key,"mode").text="major"
                time=ET.SubElement(attrs,"time")
                ET.SubElement(time,"beats").text="4"
                ET.SubElement(time,"beat-type").text="4"
                if pid=="P2":
                    ET.SubElement(attrs,"staves").text="2"
                clef=ET.SubElement(attrs,"clef",{"number":"1"})
                ET.SubElement(clef,"sign").text="G"
                ET.SubElement(clef,"line").text="2"
                if pid=="P2":
                    clef=ET.SubElement(attrs,"clef",{"number":"2"})
                    ET.SubElement(clef,"sign").text="F"
                    ET.SubElement(clef,"line").text="4"
            if pid=="P1":
                for spec in melody[i]:
                    _note(measure,*spec)
            else:
                chord=[("B",-1,3),("D",0,4),("F",0,4)] if i in (0,3) else [("C",0,4),("F",0,4),("A",0,4)]
                for j,spec in enumerate(chord):
                    _note(measure,*spec,16,chord=j>0,staff=1)
                ET.SubElement(ET.SubElement(measure,"backup"),"duration").text="16"
                _note(measure,"B",-1,2,16,staff=2)
                _note(measure,"F",0,3,16,chord=True,staff=2)
            if i==3:
                ET.SubElement(ET.SubElement(measure,"barline",{"location":"right"}),"bar-style").text="light-heavy"
    return root


def _note(measure,step,alter,octave,duration,chord=False,staff=None):
    note=ET.SubElement(measure,"note")
    if chord:
        ET.SubElement(note,"chord")
    pitch=ET.SubElement(note,"pitch")
    ET.SubElement(pitch,"step").text=step
    if alter:
        ET.SubElement(pitch,"alter").text=str(alter)
    ET.SubElement(pitch,"octave").text=str(octave)
    ET.SubElement(note,"duration").text=str(duration)
    ET.SubElement(note,"voice").text="2" if staff==2 else "1"
    ET.SubElement(note,"type").text={4:"quarter",8:"half",16:"whole"}[duration]
    if staff:
        ET.SubElement(note,"staff").text=str(staff)
    return note


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",required=True,type=Path)
    args=parser.parse_args()
    save_score(demo_score(),args.output)
    print(args.output)
