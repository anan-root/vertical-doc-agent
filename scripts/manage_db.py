"""开发期 PostgreSQL migration 管理入口。"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from construction_bidding_agent.backend.migrate import main


if __name__ == "__main__":
    raise SystemExit(main())
