"""Fixed remote administration helper used through structured SSH commands."""

from __future__ import annotations

import base64

_REMOTE_FILE_MANAGER_SOURCE = r"""
import hashlib,json,os,stat,sys,tempfile

LIMIT=1048576

def fail(message):
    print(json.dumps({"ok":False,"changed":False,"message":message},separators=(",",":")))
    raise SystemExit(1)

def safe_path(raw):
    unsafe=any(part==".." for part in raw.split("/"))
    if not raw.startswith("/") or "\x00" in raw or unsafe:
        fail("unsafe_path")
    return raw

def safe_parents(path):
    current="/"
    for part in path.strip("/").split("/")[:-1]:
        current=os.path.join(current,part)
        try:
            mode=os.lstat(current).st_mode
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
            fail("unsafe_parent")

def read_regular(path):
    flags=os.O_RDONLY|getattr(os,"O_NOFOLLOW",0)
    try:
        descriptor=os.open(path,flags)
    except OSError:
        fail("unsafe_file")
    try:
        info=os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size>LIMIT:
            fail("unsafe_file")
        data=os.read(descriptor,LIMIT+1)
        if len(data)>LIMIT:
            fail("file_too_large")
        return data
    finally:
        os.close(descriptor)

def state(path):
    safe_parents(path)
    try:
        info=os.lstat(path)
    except FileNotFoundError:
        return {"exists":False}
    if stat.S_ISLNK(info.st_mode): kind="symlink"
    elif stat.S_ISDIR(info.st_mode): kind="directory"
    elif stat.S_ISREG(info.st_mode): kind="file"
    else: kind="other"
    result={"exists":True,"kind":kind,"uid":info.st_uid,"gid":info.st_gid,"mode":stat.S_IMODE(info.st_mode)}
    if kind=="file" and info.st_size<=LIMIT:
        result["sha256"]=hashlib.sha256(read_regular(path)).hexdigest()
    return result

def atomic_write(destination,data,uid,gid,mode):
    safe_parents(destination)
    parent=os.path.dirname(destination)
    descriptor,temporary=tempfile.mkstemp(prefix=".lim-bootstrap-",dir=parent)
    try:
        os.fchmod(descriptor,mode)
        os.fchown(descriptor,uid,gid)
        os.write(descriptor,data)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor=-1
        os.replace(temporary,destination)
        directory=os.open(parent,os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if descriptor>=0: os.close(descriptor)
        try: os.unlink(temporary)
        except FileNotFoundError: pass

action=sys.argv[1]
if action=="state":
    path=safe_path(sys.argv[2])
    print(json.dumps({"ok":True,"changed":False,"state":state(path)},separators=(",",":")))
elif action=="ensure_dir":
    path=safe_path(sys.argv[2]);uid=int(sys.argv[3]);gid=int(sys.argv[4]);mode=int(sys.argv[5],8)
    safe_parents(path)
    changed=False
    try:
        info=os.lstat(path)
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            fail("unsafe_directory")
    except FileNotFoundError:
        os.makedirs(path,mode=mode);changed=True;info=os.lstat(path)
    if info.st_uid!=uid or info.st_gid!=gid: os.chown(path,uid,gid);changed=True
    if stat.S_IMODE(info.st_mode)!=mode: os.chmod(path,mode);changed=True
    print(json.dumps({"ok":True,"changed":changed},separators=(",",":")))
elif action=="install":
    staged=safe_path(sys.argv[2]);destination=safe_path(sys.argv[3]);uid=int(sys.argv[4]);gid=int(sys.argv[5]);mode=int(sys.argv[6],8)
    data=read_regular(staged);current=state(destination);digest=hashlib.sha256(data).hexdigest()
    if current.get("exists") and current.get("kind")!="file": fail("unsafe_destination")
    correct=current.get("sha256")==digest and current.get("uid")==uid
    correct=correct and current.get("gid")==gid and current.get("mode")==mode
    changed=not correct
    if changed: atomic_write(destination,data,uid,gid,mode)
    print(json.dumps({"ok":True,"changed":changed,"sha256":digest},separators=(",",":")))
elif action=="merge_key":
    staged=safe_path(sys.argv[2]);destination=safe_path(sys.argv[3]);uid=int(sys.argv[4]);gid=int(sys.argv[5]);mode=int(sys.argv[6],8);marker=sys.argv[7]
    entry=read_regular(staged).decode("utf-8")
    if "\n" in entry.strip("\n") or not entry.endswith("\n"): fail("invalid_key_entry")
    current=state(destination)
    if current.get("exists") and current.get("kind")!="file": fail("unsafe_destination")
    existing=read_regular(destination).decode("utf-8") if current.get("exists") else ""
    lines=[line for line in existing.splitlines() if marker not in line]
    desired=("\n".join(lines)+("\n" if lines else "")+entry)
    data=desired.encode("utf-8");digest=hashlib.sha256(data).hexdigest()
    correct=current.get("sha256")==digest and current.get("uid")==uid
    correct=correct and current.get("gid")==gid and current.get("mode")==mode
    changed=not correct
    if changed: atomic_write(destination,data,uid,gid,mode)
    print(json.dumps({"ok":True,"changed":changed},separators=(",",":")))
else:
    fail("invalid_action")
""".strip()

_ENCODED_REMOTE_FILE_MANAGER = base64.b64encode(
    _REMOTE_FILE_MANAGER_SOURCE.encode("utf-8")
).decode("ascii")


def remote_file_manager_command(
    python_path: str,
    action: str,
    *arguments: str,
) -> tuple[str, ...]:
    """Return a fixed encoded helper invocation with structured arguments."""
    loader = (
        "import base64;exec(compile(base64.b64decode("
        f"'{_ENCODED_REMOTE_FILE_MANAGER}'),'<lim-bootstrap>','exec'))"
    )
    return (python_path, "-c", loader, action, *arguments)
