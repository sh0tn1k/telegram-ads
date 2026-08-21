"""Fresh-consumer launch: print typed inventory twice-comparable JSON."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tgads_surface import inventory  # noqa: E402


def main() -> None:
    payload = inventory()
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
