"""Command line entry point: `python -m vrag <command>`. Commands are added in later tasks."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    print("vrag: no commands implemented yet", file=sys.stderr)
    return 0 if not argv else 2
