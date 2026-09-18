"""Exit code constants shared by every ContentOS CLI subcommand.

See the design spec's "Global Constraints" section for the meaning of
each code: 0 ok, 1 subcommand stub not implemented yet, 2 usage, 3
confirmation required, 4 missing key, 5 upstream failure, 6 cost cap,
7 verification failed.
"""
from __future__ import annotations

EXIT_OK = 0
EXIT_STUB = 1
EXIT_USAGE = 2
EXIT_CONFIRM = 3
EXIT_KEYS = 4
EXIT_UPSTREAM = 5
EXIT_COST = 6
EXIT_VERIFY = 7
