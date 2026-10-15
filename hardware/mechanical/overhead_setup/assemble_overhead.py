"""Place both independent fixed-height fixtures in native reference assemblies."""
import json
import build_overhead as build
import assemble_solidworks as assembly
import layout


def main():
    build.initialize()
    records={}
    for profile in ('tx4','tx8'):
        name='Overhead_'+('4x4' if profile=='tx4' else '8x8')+'_assembly'
        records[name]=layout.placements(profile)
        assembly.make_assembly(name,records[name])
    (layout.OUT/'assemblies.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
