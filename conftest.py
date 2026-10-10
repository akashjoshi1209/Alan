"""Put ``src`` on ``sys.path`` so the suite runs from a bare checkout.

Alan is a bare source tree with no packaging step: there is no ``setup.py``,
no ``pyproject.toml``, nothing to install, and ``src`` exists precisely so the
package is importable without polluting the host's site-packages. That leaves
``src`` out of the path for anything that does not set ``PYTHONPATH``, which
includes the test command Alan's own coding provider infers for this repo::

    python -m pytest -q

So a correct invocation reported a confusing collection error instead of
running the tests. Adding the path here makes the inferred command actually
work, with no environment setup and no install step.

Deliberately tiny: pytest imports this before collecting anything, so anything
slow or failing here breaks the whole suite with an unhelpful traceback.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if SRC.is_dir() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# A test that reaches the chat or ask surface must never spawn the real local
# sidecars (see alan.sidecars): those are detached OS processes with their own
# lifetimes, not fixtures. The ensure is a product behaviour and is tested on
# its own with mocks; everywhere else it stays switched off -- the same
# ALAN_NO_* switches the launcher has always honoured.
os.environ.setdefault("ALAN_NO_OLLAMA", "1")

# Memory is a SQLite file now, so nothing is spawned -- but a test run must not
# write episodic records into the live store either. Pointing it at a scratch
# directory keeps every suite run hermetic without disabling memory, which
# would stop the tests exercising the behaviour they are named after.
if not os.environ.get("ALAN_MEMORY_DIR"):
    import tempfile

    os.environ["ALAN_MEMORY_DIR"] = tempfile.mkdtemp(prefix="alan-test-memory-")
