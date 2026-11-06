"""Keep preparation process groups bound to the agent and reap the direct child."""

import os
import signal
import subprocess
import sys
import time

parent_pid = int(sys.argv[1])
interrupted = False


def interrupt(_signal, _frame):
    global interrupted
    interrupted = True


signal.signal(signal.SIGTERM, interrupt)
if os.getppid() != parent_pid or interrupted:
    raise SystemExit(125)
# The wrapper must survive the group's SIGKILL so it can wait/reap the child.
process = subprocess.Popen(sys.argv[2:], start_new_session=True)
try:
    while process.poll() is None:
        if interrupted or os.getppid() != parent_pid:
            break
        time.sleep(0.02)
finally:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()
raise SystemExit(process.returncode)
