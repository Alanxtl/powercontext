"""SDD context module: specification-driven development context for AI coding agents.

The module wires three required technologies together:

* **seekdb** stores the engineering corpus (specifications, code symbols, tests)
  and answers the retrieval queries that build the development context.
* **PowerContext** keeps the durable task state (progress, verification results)
  so a restarted module can read it back and continue.
* **MiniMax** performs the actual code modification with that context.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
