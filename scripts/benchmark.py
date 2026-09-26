"""Opt-in synthetic filesystem benchmark; never opens installed worlds or Docker."""
import argparse
import json
from pathlib import Path
import platform
import statistics
import tempfile
import time
import tracemalloc

from gamestack import backup, restore
from gamestack.pack import load_pack
from gamestack.runtime import Runtime


def measure(case):
    with tempfile.TemporaryDirectory(prefix="gamestack-benchmark-") as temporary:
        runtime = Runtime(Path(temporary).resolve() / "instances")
        pack = load_pack(Path(__file__).resolve().parents[1] / "packs/example/pack.yaml")
        directory = runtime.prepare(pack, "bench", {"SERVER_NAME": "benchmark", "SERVER_PASSWORD": "synthetic-only"})
        data = directory / "data"
        if case == "large":
            with (data / "world").open("wb") as stream:
                chunk = b"world123" * (1024 * 128)
                for _ in range(256):
                    stream.write(chunk)
        elif case == "small":
            for i in range(10_000):
                (data / str(i)).write_bytes(b"world123" * 128)
        else:
            for _ in range(100):
                data /= "d"
                data.mkdir()
            (data / "world").write_bytes(b"synthetic world")
        result = {}
        tracemalloc.start()
        def timed(label, function):
            start = time.perf_counter()
            value = function()
            result[label] = time.perf_counter() - start
            return value
        try:
            timed("inventory_s", lambda: backup.inventory(directory))
            artifact = timed("create_s", lambda: backup.create(directory, "bench", pack, "exited"))
            timed("verify_s", lambda: backup.verify(artifact, "bench"))
            timed("stage_s", lambda: restore.stage(directory, "bench", artifact, pack, True))
            result["peak_python_bytes"] = tracemalloc.get_traced_memory()[1]
            result["archive_bytes"] = artifact.stat().st_size
        finally:
            tracemalloc.stop()
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    print(json.dumps({"python": platform.python_version(), "host": platform.platform(),
                      "memory": "tracemalloc peak Python allocations; tracing enabled during timings"}), flush=True)
    for case in ("large", "small", "deep"):
        samples = [measure(case) for _ in range(args.runs)]
        print(json.dumps({"case": case, "samples": samples,
                          "median": {key: statistics.median(s[key] for s in samples) for key in samples[0]}}), flush=True)
