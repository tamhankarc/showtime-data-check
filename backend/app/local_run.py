"""Load ignored local config, then run one API/worker process."""
import json
import os
from pathlib import Path
import uvicorn


def load_environment(path=None):
    path = Path(path) if path else Path(__file__).resolve().parents[2] / "private" / "backend.env"
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key] = json.loads(value) if value.startswith('"') else value


if __name__ == "__main__":
    load_environment()
    uvicorn.run("app.main:app", host="127.0.0.1", port=8001, workers=1, access_log=False)
