"""Dev entrypoint — run the MCPWatch web app.

    python run.py            # serves http://localhost:8000

Fixes cwd/sys.path so it works no matter where it's launched from, then starts uvicorn.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

if __name__ == "__main__":
    import uvicorn
    # 0.0.0.0 so it works inside a container / on a PaaS; still reachable as localhost locally.
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("mcpwatch.api:app", host=host, port=port, reload=False)
