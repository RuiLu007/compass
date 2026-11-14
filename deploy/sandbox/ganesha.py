"""Lab NAS: sixteen separate 64 MiB Linux filesystems served by NFS-Ganesha."""

import os
import stat
import subprocess
from pathlib import Path

slots = int(os.environ.get("COMPASS_NAS_SLOTS", "16"))
if not 1 <= slots <= 256:
    raise ValueError("slot limit")
config = """NFS_CORE_PARAM { Protocols = 4; NFS_Port = 2049; Enable_UDP = false;
 Enable_NLM = false; Enable_RQUOTA = false; }
NFS_KRB5 { Active_krb5 = false; }
NFSV4 { Graceless = true; }
LOG { Default_Log_Level = WARN;
 Facility { name = FILE; destination = "/dev/stderr"; enable = active; } }
"""
# Docker Desktop exposes eight loop nodes by default; create additional device nodes
# for the separately bounded slot filesystems. Kernel ownership stays with the NAS service.
for index in range(slots + 32):
    device = Path(f"/dev/loop{index}")
    if not device.exists():
        os.mknod(device, stat.S_IFBLK | 0o600, os.makedev(7, index))
Path("/data").mkdir(exist_ok=True)
Path("/run/ganesha").mkdir(parents=True, exist_ok=True)
for slot in range(slots):
    name = f"slot{slot:03d}"
    backing = Path("/data") / (name + ".img")
    mount = Path("/exports") / name
    mount.mkdir(parents=True, exist_ok=True)
    if not backing.exists():
        subprocess.run(["truncate", "-s", "64M", str(backing)], check=True)
        subprocess.run(["mkfs.ext4", "-q", "-m", "0", str(backing)], check=True)
    if not os.path.ismount(mount):
        subprocess.run(
            ["mount", "-o", "loop,nodev,nosuid,noexec", str(backing), str(mount)], check=True
        )
    os.chown(mount, 1000, 1000)
    # ext4 creates a root-owned recovery directory; keep it outside user access.
    recovery = mount / "lost+found"
    if recovery.exists() and not any(recovery.iterdir()):
        recovery.rmdir()
    config += f'''EXPORT {{ Export_Id = {slot + 1}; Path = "{mount}"; Pseudo = "/slots/{name}";
 Access_Type = RW; Squash = All_Squash; Anonymous_uid = 1000; Anonymous_gid = 1000;
 Protocols = 4; Transports = TCP; SecType = sys; FSAL {{ Name = VFS; }} }}\n'''
Path("/etc/ganesha/ganesha.conf").write_text(config)
Path("/run/dbus").mkdir(exist_ok=True)
Path("/run/dbus/pid").unlink(missing_ok=True)
subprocess.run(["dbus-daemon", "--system", "--fork"], check=True)
os.execvp(
    "ganesha.nfsd", ["ganesha.nfsd", "-F", "-f", "/etc/ganesha/ganesha.conf", "-L", "/dev/stderr"]
)
