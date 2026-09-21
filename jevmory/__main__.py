"""``python3 -m jevmory`` — same CLI as the console script.

The plugin's ``bin/jevmory`` wrapper and the docs' ``python3 -m``
one-liners all land here; the real wiring stays ``jevmory.cli:main``.
"""

from __future__ import annotations

import sys

from jevmory.cli import main

if __name__ == "__main__":
    sys.exit(main())
