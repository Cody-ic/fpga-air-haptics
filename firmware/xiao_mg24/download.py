"""Resumable bounded-range download for large official development packages."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import time
import urllib.request


def download(url, destination, checksum=None):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if checksum and destination.exists():
        with destination.open('rb') as existing:
            if hashlib.file_digest(existing, 'sha256').hexdigest() == checksum:
                return
    with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=60) as response:
        size = int(response.headers["Content-Length"])
    if destination.exists() and destination.stat().st_size == size:
        with destination.open('rb') as existing:
            digest = hashlib.file_digest(existing, 'sha256').hexdigest()
        if not checksum or digest == checksum:
            return
    parts = destination.with_suffix(destination.suffix + ".parts")
    parts.mkdir(exist_ok=True)
    block = 4 * 1024 * 1024

    def fetch(start):
        end = min(size, start + block) - 1
        path = parts / str(start)
        if path.exists() and path.stat().st_size == end - start + 1:
            return path
        for attempt in range(8):
            try:
                request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
                with urllib.request.urlopen(request, timeout=15) as response:
                    if response.status != 206:
                        raise RuntimeError("Server did not honor the bounded Range request")
                    started = time.monotonic()
                    chunks = []
                    while sum(map(len, chunks)) < end - start + 1:
                        if time.monotonic() - started > 45:
                            raise TimeoutError('Range transfer deadline exceeded')
                        chunk = response.read(min(65536, end - start + 1 - sum(map(len, chunks))))
                        if not chunk:
                            break
                        chunks.append(chunk)
                    data = b''.join(chunks)
                    if len(data) != end - start + 1:
                        raise RuntimeError("Incomplete range")
                path.write_bytes(data)
                print(f"Downloaded {end + 1}/{size}: {destination.name}", flush=True)
                return path
            except Exception:
                if attempt == 7:
                    raise
                time.sleep(min(2 ** attempt, 20))

    with ThreadPoolExecutor(max_workers=8) as executor:
        paths = list(executor.map(fetch, range(0, size, block)))
    temporary = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    with temporary.open("wb") as output:
        for path in paths:
            data = path.read_bytes()
            digest.update(data)
            output.write(data)
    if checksum and digest.hexdigest() != checksum:
        raise RuntimeError("SHA-256 checksum mismatch")
    temporary.replace(destination)
    print(f"Complete: {destination} SHA-256={digest.hexdigest()}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("destination")
    parser.add_argument("--sha256")
    args = parser.parse_args()
    download(args.url, args.destination, args.sha256)
