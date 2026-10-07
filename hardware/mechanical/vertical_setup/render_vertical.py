"""Blender rendering of actual P4 STL geometry, not an illustrative CAD mockup."""
import json
import argparse
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
import render_preview as render

P=json.loads((HERE/'parameters.json').read_text(encoding='utf-8'))
render.OUT=HERE.parent/'dist'/P['revision']
assemblies=json.loads((render.OUT/'assemblies.json').read_text(encoding='utf-8'))
# A keepout is reserved air, not a printable cover or a real connector model.
assemblies={name:[e for e in entries if e['name']!='REF_tx_rear_clearances']
            for name,entries in assemblies.items()}
def key(e): return (e['name'],tuple(e['rotation']),tuple(e['translation_mm']))
common={key(e) for e in assemblies['Vertical_wrist_assembly']}
cartridge=[e for e in assemblies['Vertical_receiver_assembly'] if key(e) not in common]
scenes={
    'vertical_wrist':(assemblies['Vertical_wrist_assembly'],(.29,-.33,.26),(0,-.01,.078),.29),
    'vertical_receiver':(assemblies['Vertical_receiver_assembly'],(.29,-.33,.26),(0,-.01,.078),.29),
    'receiver_cartridge':(cartridge,(.18,.16,.21),(0,-.06,.12),.18),
    'rear_wiring_clearance':(assemblies['Vertical_wrist_assembly'],(-.25,.31,.24),(0,-.01,.078),.29)
}
parser=argparse.ArgumentParser(); parser.add_argument('--only',choices=scenes)
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
for name,arguments in scenes.items():
    if args.only is None or args.only==name: render.render(name,*arguments)
