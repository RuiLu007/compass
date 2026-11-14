"""Fixed entrypoint for untrusted Python; isolation is provided by the container runtime."""

import json
import os
import resource
import sys
from pathlib import Path

os.umask(0o077)
resource.setrlimit(resource.RLIMIT_FSIZE, (512 * 1024, 512 * 1024))
resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
source = Path("/workspace/source.py").read_text()
if len(source.encode()) > 16 * 1024:
    raise ValueError("source limit")
raw = Path("/workspace/input.json").read_bytes()
if len(raw) > 128 * 1024:
    raise ValueError("input limit")
namespace = {"__name__": "__main__", "data": json.loads(raw), "result": None}
with open("/workspace/stdout.log", "w") as log:
    sys.stdout = sys.stderr = log
    exec(compile(source, "/workspace/source.py", "exec"), namespace)
    payload = json.dumps(namespace["result"], ensure_ascii=False, allow_nan=False).encode()
    if len(payload) > 64 * 1024:
        raise ValueError("result limit")
    Path("/workspace/result.json").write_bytes(payload)
