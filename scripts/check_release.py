"""Verify ignored local runtime data and secrets cannot enter the source release."""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    files = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, text=True).splitlines()
    values = {}
    config = ROOT / "private/backend.env"
    if config.exists():
        for line in config.read_text(encoding="utf-8").splitlines():
            key, value = line.split("=", 1)
            if any(part in key for part in ("SECRET", "PASSWORD", "API_KEY", "DATABASE_URL", "RELAY_URL")):
                value = json.loads(value)
                if len(value) >= 16:
                    values[key] = value.encode()
    violations = []
    for name in set(files):
        path = ROOT / name
        if not path.is_file():
            continue
        if name.startswith(("private/", "planning/screenshots/")) or "/node_modules/" in name or name.endswith(".env.local") or "/.venv/" in name:
            violations.append((name, "runtime/private file"))
        content = path.read_bytes()
        for key, value in values.items():
            if value in content:
                violations.append((name, key))
    if violations:
        print(json.dumps({"release_check": "failed", "locations_only": violations}))
        raise SystemExit(1)
    print(json.dumps({"release_check": "passed", "source_files": len(set(files)), "private_values_checked": len(values)}))


if __name__ == "__main__":
    main()
