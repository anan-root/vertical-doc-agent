"""启动前后端 MVP 开发服务。"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "construction_bidding_agent.backend.app:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )
