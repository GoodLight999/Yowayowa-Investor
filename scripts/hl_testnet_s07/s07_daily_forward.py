#!/usr/bin/env python3
"""Stop marker for the rejected S-07 executor, including legacy cron wrappers.

No credentials, network, locks, ledgers or report writers are loaded here.
The original source is archived as non-executable evidence; see the decision log.
"""

import sys


def main() -> int:
    print(
        "DISABLED: t_f840d5bc rejected the unsafe S-07 forward executor. "
        "No order or dry-run ledger write was performed. "
        "See docs/HL_EXEC_DISPOSITION_20261108.md; replacement needs independent review.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
