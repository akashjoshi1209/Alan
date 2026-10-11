"""Download the OmniVoice TTS model, one connection at a time.

Executed by ``env\\tts\\python\\python.exe`` from ``scripts\\pull-tts-model.ps1``,
exactly as ``whisper_recorder.py`` is executed by the whisper interpreter: it
lives in ``src\\`` but is never *imported* from Alan, because that interpreter
deliberately cannot reach Alan's packages.

Why not ``huggingface_hub.snapshot_download``
---------------------------------------------
It fans out with a thread pool. On an ordinary connection that is free speed;
here it is the difference between twelve minutes and never finishing. Quick
Hearth's web-security driver inserts a fixed ~10.5 s into the TLS handshake of
every new outbound web connection (the same tax that made ``alan ask`` time out
until ``connect_timeout`` was raised), so cost is charged **per connection**,
not per byte. A pool of workers pays it repeatedly and in parallel, and the
hub's own progress reporting stalls in a way that looks exactly like a dead
network -- it is not dead, it is just paying the toll N times over.

So this does the opposite: one ``requests.Session`` for every file. Because
``Session`` pools its connections, the handshake is paid once for
``huggingface.co`` and once for the CDN it redirects to, and every file after
that rides an already-open socket. Measured on this machine: 5 MB in 28.6 s
while the handshake amortised, then 15 MB in the next 3.5 s (~4.3 MB/s) -- so
the ~3.0 GB model lands in roughly twelve minutes instead of stalling out.

Resume, not restart
-------------------
Each file is written to ``<name>.partial`` and moved into place only when
complete, and an existing ``.partial`` is continued with a ``Range`` request.
An interrupted run therefore costs the bytes already fetched nothing -- which
matters more here than on a fast link, because a run that dies at 2.9 GB and
starts over at zero is a run that never gets finished.

Both checkpoints are required. The audio tokenizer is not an optional extra:
it turns the model's tokens into waveforms, so a "downloaded" model without it
loads and then cannot produce a sound.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

#: Unauthenticated requests are rate limited by HuggingFace. Set HF_TOKEN to
#: raise the limit and to reach gated repos; read from the environment so no
#: token is ever written into a script or a command line.
def _token() -> str | None:
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")


def _file_list(repo: str, session) -> list[str]:
    """Filenames in the repo, from the JSON API -- no hub client needed.

    Kept to one small request rather than pulling in the hub's resolver, which
    would re-create the connection pattern this script exists to avoid.
    """
    import requests

    url = f"https://huggingface.co/api/models/{repo}"
    headers = {"Authorization": f"Bearer {_token()}"} if _token() else {}
    response = session.get(url, headers=headers, timeout=60)
    response.raise_for_status()
    return [item["rfilename"] for item in response.json().get("siblings", [])]


def _download(session, url: str, dest: Path, *, label: str) -> int:
    """Stream one file to ``dest`` via a ``.partial`` neighbour. Returns bytes."""
    import requests

    headers = {"Authorization": f"Bearer {_token()}"} if _token() else {}
    partial = dest.with_name(dest.name + ".partial")

    # Resume from whatever is already on disk. The CDN honours Range (verified
    # while diagnosing the timeout), so a restart costs nothing but the tail.
    offset = partial.stat().st_size if partial.exists() else 0
    if offset:
        headers["Range"] = f"bytes={offset}-"

    started = time.perf_counter()
    with session.get(url, headers=headers, stream=True, timeout=(30, 300),
                     allow_redirects=True) as response:
        # 416 means the local copy is already the whole file -- finish it off
        # rather than treating a completed download as a failure.
        if response.status_code == 416:
            partial.replace(dest)
            return offset
        # A 200 to a Range request means the server ignored the range and is
        # sending from byte 0; appending would corrupt the file, so truncate.
        if offset and response.status_code != 206:
            offset = 0
            partial.unlink(missing_ok=True)
        response.raise_for_status()

        total = int(response.headers.get("Content-Length") or 0) + offset
        mode = "ab" if offset else "wb"
        shown = offset
        last_report = time.perf_counter()

        with partial.open(mode) as handle:
            for chunk in response.iter_content(chunk_size=1 << 20):
                if not chunk:
                    continue
                handle.write(chunk)
                shown += len(chunk)
                now = time.perf_counter()
                # Rate-limited to ~1/s: a line per chunk on a 2.28 GB file is
                # thousands of lines of noise that buries the real signal.
                if now - last_report >= 1.0:
                    elapsed = now - started
                    speed = (shown - offset) / max(elapsed, 1e-6) / (1 << 20)
                    percent = 100.0 * shown / total if total else 0.0
                    print(
                        f"  {label}: {shown / (1 << 20):8.0f} / "
                        f"{total / (1 << 20):8.0f} MB  {percent:5.1f}%  "
                        f"{speed:5.2f} MB/s",
                        flush=True,
                    )
                    last_report = now

    partial.replace(dest)
    elapsed = time.perf_counter() - started
    print(f"  {label}: done ({shown / (1 << 20):.0f} MB in {elapsed:.0f}s)",
          flush=True)
    return shown


def main() -> int:
    parser = argparse.ArgumentParser(description="download the OmniVoice model")
    parser.add_argument("--model", default="k2-fsa/OmniVoice")
    parser.add_argument("--dest", required=True, help="local directory to fill")
    parser.add_argument("--list", action="store_true", help="list and exit")
    args = parser.parse_args()

    import requests

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)

    # One Session for the whole run: this is the entire point of the module.
    # requests.Session pools connections per host, so huggingface.co is
    # handshaked once and the CDN once, however many files follow.
    session = requests.Session()
    session.headers.update({"User-Agent": "Alan-TTS/1"})

    files = _file_list(args.model, session)
    if not files:
        print(f"no files reported for {args.model}", file=sys.stderr)
        return 1

    if args.list:
        for name in files:
            print(name)
        return 0

    # Download the two checkpoints first. They are 97% of the bytes and the
    # only ones that make the model usable, so a run cut short at least leaves
    # the small configuration files already in place for the next attempt.
    order = sorted(files, key=lambda n: (not n.endswith(".safetensors"), n))

    for name in order:
        target = dest / name
        if target.exists():
            print(f"  {name}: already present", flush=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://huggingface.co/{args.model}/resolve/main/{name}"
        try:
            _download(session, url, target, label=name)
        except requests.RequestException as exc:
            # Carry on: a failure on one file must not abandon the ones that
            # already landed, and the verifier below reports what is missing.
            print(f"  {name}: FAILED ({exc})", file=sys.stderr, flush=True)

    session.close()

    # Verify the two that cannot be substituted for.
    missing = [
        name for name in ("model.safetensors",
                          "audio_tokenizer/model.safetensors")
        if not (dest / name).exists()
    ]
    if missing:
        print("missing required checkpoint(s): " + ", ".join(missing),
              file=sys.stderr)
        return 1

    total = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file())
    print(f"\nmodel ready: {total / (1 << 30):.2f} GB at {dest}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
