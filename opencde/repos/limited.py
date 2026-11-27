# This file was generated with the assistance of an AI coding tool.
"""Run a program under an address-space limit (POSIX), as the IFC workers run under theirs (ADR 0030).

    python -I -m opencde.repos.limited MEGABYTES PROGRAM ARGUMENT...

The limit is set, and the process becomes the program (``os.execv``), so that its exit code and its output are the
program's own.
"""

from __future__ import annotations

import os
import sys


def main(argv: list[str]) -> None:
    from ifc_db.worker import limit_memory

    limit_memory(int(argv[0]))
    program = argv[1]
    if os.sep not in program:
        found = [os.path.join(folder, program) for folder in os.environ.get("PATH", "").split(os.pathsep)]
        program = next((path for path in found if os.access(path, os.X_OK)), program)
    os.execv(program, argv[1:])  # noqa: S606 - the server's own command line: perl and ifcmerge


if __name__ == "__main__":
    main(sys.argv[1:])
