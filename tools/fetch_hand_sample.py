"""Download one public EgoPressure annotation/pressure pair with fixed hashes.

Data stays outside source control. Terms and source:
https://huggingface.co/datasets/eth-siplab/EgoPressure
https://github.com/eth-siplab/EgoPressure/blob/master/docs/DATA.md
Dataset CC BY-NC-SA 4.0; hand-model-derived annotations retain MANO terms.
No image/video download and no redistribution of these files in release ZIPs.
"""
import argparse
import hashlib
from pathlib import Path
import urllib.request

NAME = 'p_001_index_press_pull_x5_right'
BASE = 'https://huggingface.co/datasets/eth-siplab/EgoPressure/resolve/main/data/p_001/'+NAME+'/'
FILES = {
    'annotation.parquet': (12620091, 'c6081d34de38202d7c6cfa3d566aac5c3d8b85ec3482daf0934aac8b376fa5ec'),
    'pressure.parquet': (1166658, 'ab7063adb72a986d0e3594c0a85ccd849b29eb216fe5df791b054aa10728c97c'),
}


def fetch(output):
    output = Path(output)/NAME
    output.mkdir(parents=True, exist_ok=True)
    for name, (size, digest) in FILES.items():
        target = output/name
        if target.is_file() and target.stat().st_size == size and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
            print('VERIFIED', target, flush=True)
            continue
        temporary = output/(name+'.part')
        request = urllib.request.Request(BASE+name, headers={'User-Agent': 'TouchSee-Research/1.0'})
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open('wb') as stream:
            while True:
                chunk = response.read(1024*1024)
                if not chunk:
                    break
                stream.write(chunk)
                if stream.tell() > size:
                    raise ValueError('Unexpected dataset file size')
        if temporary.stat().st_size != size or hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
            raise ValueError('Dataset source changed or download incomplete: '+name)
        temporary.replace(target)
        print('DOWNLOADED AND VERIFIED', target, flush=True)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('.runtime/datasets'))
    args = parser.parse_args()
    print(fetch(args.output))
