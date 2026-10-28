"""Allow ``python -m sdd_context`` to run the module command line."""

from __future__ import annotations

from sdd_context.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
