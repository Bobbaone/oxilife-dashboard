import os
import pwd
import sys
from pathlib import Path


def drop_privileges() -> None:
    if os.geteuid() != 0:
        return
    account = pwd.getpwnam("poolmonitor")
    data_dir = Path("/app/data")
    data_dir.mkdir(parents=True, exist_ok=True)
    for path in (data_dir, *data_dir.rglob("*")):
        try:
            os.lchown(path, account.pw_uid, account.pw_gid)
        except FileNotFoundError:
            pass
    os.initgroups(account.pw_name, account.pw_gid)
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)


drop_privileges()
os.execvp(sys.argv[1], sys.argv[1:])
