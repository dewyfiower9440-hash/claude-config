"""Helper of test_inproc.py (N-T9): three runs of one script, then how they were run."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent)
)  # python -I drops the script's folder
import inproc

for _ in range(3):
    inproc.run(Path(sys.argv[1]), ["x"], "")
print("real", inproc.counts["real"], "inproc", inproc.counts["inproc"])
