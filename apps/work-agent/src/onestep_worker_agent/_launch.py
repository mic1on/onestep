"""Worker launch gate. The supervisor persists our PID before releasing stdin.

If the agent dies between spawning and persisting, the pipe reaches EOF and no
business worker starts. After release, exec preserves the PID for recovery.
"""

import os
import sys

if sys.stdin.buffer.read(1) != b"1":
    raise SystemExit(125)
with open(os.devnull, "rb") as null:
    os.dup2(null.fileno(), 0)
os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
