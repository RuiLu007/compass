"""Runs in a fresh trusted container after compute deletion; fixed paths and bounded reads."""

import base64
import json
import os
import stat
import sys


def read(name, limit):
    descriptor = os.open("/workspace/" + name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise ValueError("invalid file")
        chunks = bytearray()
        while True:
            block = os.read(descriptor, min(65536, limit + 1 - len(chunks)))
            if not block:
                return bytes(chunks)
            chunks.extend(block)
            if len(chunks) > limit:
                raise ValueError("file limit")
    finally:
        os.close(descriptor)


def clean(directory):
    # The volume is mounted alone and no compute resource remains. Never follow symlinks.
    os.chmod(directory, 0o700, follow_symlinks=False)
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                clean(entry.path)
                os.rmdir(entry.path)
            else:
                os.unlink(entry.path)


if len(sys.argv) > 1 and sys.argv[1] == "clean":
    clean("/workspace")
    print(json.dumps({"clean": not any(os.scandir("/workspace"))}))
else:
    result = {"result": json.loads(read("result.json", 64 * 1024)), "chart": None}
    try:
        result["chart"] = base64.b64encode(read("chart.png", 512 * 1024)).decode()
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        result["chartRejected"] = True
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
