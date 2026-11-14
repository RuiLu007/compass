"""Provision only known, labelled Docker NFS volumes; run on the sandbox host as admin."""

import argparse
import json
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("--nas-address", default="172.29.18.2")
parser.add_argument("--slots", type=int, default=16)
parser.add_argument("--prefix", default="compass-sandbox-slot")
args = parser.parse_args()
if not 1 <= args.slots <= 256:
    raise SystemExit("slots must be between 1 and 256")
for slot in range(args.slots):
    name = f"{args.prefix}{slot:03d}"
    expected = {
        "type": "nfs",
        "o": f"addr={args.nas_address},nfsvers=4,rw,hard,timeo=50,retrans=2",
        "device": f":/slots/slot{slot:03d}",
    }
    inspected = subprocess.run(["docker", "volume", "inspect", name], capture_output=True)
    if inspected.returncode == 0:
        value = json.loads(inspected.stdout)[0]
        if (
            value["Options"] != expected
            or value["Labels"].get("compass.sandbox.pool") != args.prefix
        ):
            raise SystemExit(f"Refusing incompatible existing volume: {name}")
    else:
        command = [
            "docker",
            "volume",
            "create",
            "--driver",
            "local",
            "--label",
            f"compass.sandbox.pool={args.prefix}",
        ]
        for key, value in expected.items():
            command += ["--opt", f"{key}={value}"]
        subprocess.run([*command, name], check=True)
print(f"Validated {args.slots} private NFS volumes")
