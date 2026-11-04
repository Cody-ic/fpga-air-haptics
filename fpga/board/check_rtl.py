"""Run existing FPGA benches with Icarus on Windows, without any board access."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
DEFAULT=['tb_hap2_rx','tb_fix_units','tb_traj','tb_shape','tb_phase','tb_out',
         'tb_local','tb_board','tb_top8','tb_hap2_top','tb_review_edges','tb_focus_propagation', 'tb_stop_commit']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iverilog',required=True)
    parser.add_argument('benches',nargs='*',default=DEFAULT)
    args=parser.parse_args()
    compiler=Path(args.iverilog)
    folder=ROOT/'.runtime/rtl-alignment'
    folder.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,TMP=str(folder),TMPDIR=str(folder))
    def run(name):
        path=ROOT/'fpga/tb'/f'{name}.v'
        if not path.is_file() or path.parent != ROOT/'fpga/tb':
            raise ValueError(name)
        target=folder/f'{name}.vvp'
        logpath=folder/f'{name}.log'
        with logpath.open('w') as log:
            result=subprocess.run([str(compiler),'-g2012','-s',name,'-o',str(target),
                                   *([f'-P{name}.SIM_SCALE=10'] if name in ('tb_top8','tb_hap2_top') else []),
                                   *map(str,sorted((ROOT/'fpga/rtl').glob('*.v'))),str(path)],
                                  cwd=ROOT/'fpga',env=env,stdout=log,stderr=subprocess.STDOUT)
            if result.returncode==0:
                result=subprocess.run([str(compiler.with_name('vvp.exe')),'-i',str(target)],
                                      cwd=ROOT/'fpga',stdout=log,stderr=subprocess.STDOUT,timeout=900)
        text=logpath.read_text(errors='replace')
        ok=result.returncode==0 and not any(word in text for word in ('FAIL','FATAL','ERROR:'))
        print(('PASS' if ok else 'FAIL'),name,flush=True)
        return ok
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(run,args.benches))
    raise SystemExit(0 if all(results) else 1)


if __name__=='__main__': main()
