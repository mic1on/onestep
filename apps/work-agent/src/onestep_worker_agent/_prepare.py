"""Keep POSIX preparation process groups bound to the owning agent's lifetime."""

import os
import signal
import subprocess
import sys
import time

parent_pid = int(sys.argv[1])
if os.getppid() != parent_pid:
    raise SystemExit(125)
process = subprocess.Popen(sys.argv[2:])
while process.poll() is None:
    if os.getppid() != parent_pid:
        os.killpg(os.getpgrp(), signal.SIGKILL)
    time.sleep(0.05)
raise SystemExit(process.returncode)
