"""Generate ignored local configuration without disclosing credentials to output.

This is an operational bootstrap, not a production account-management endpoint.
Existing config is preserved. --reuse-admin-credentials reads only the authorized
local provider secret files, never AWS or the source database.
"""
import argparse
import json
from pathlib import Path
import secrets
from .security import password_hash


ROOT = Path(__file__).resolve().parents[2]


def env_text(values):
    # Docker/Next .env supports quoted values; generated values and source JSON secrets
    # are JSON-escaped and stay in ignored files, never command arguments or logs.
    return "\n".join(f"{key}={json.dumps(str(value), ensure_ascii=False)}" for key, value in values.items()) + "\n"


def setup(reuse=False):
    private = ROOT / "private"
    private.mkdir(exist_ok=True)
    target = private / "backend.env"
    if target.exists():
        if not reuse:
            print("Local configuration already exists; no credentials were changed.")
            return
        values = {key: json.loads(value) for line in target.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#") for key, value in [line.split("=", 1)]}
        database_password = proxy_secret = admin_password = viewer_password = None
    else:
        database_password, proxy_secret = secrets.token_hex(32), secrets.token_urlsafe(48)
        admin_password, viewer_password = secrets.token_urlsafe(20), secrets.token_urlsafe(20)
        values = {"ENVIRONMENT": "development", "DATABASE_URL": f"postgresql+psycopg://data_check:{database_password}@127.0.0.1:5434/showtime_data_check",
                  "APP_ORIGIN": "http://localhost:3001", "PROXY_SECRET": proxy_secret, "ADMIN_USERNAME": "administrator", "ADMIN_PASSWORD_HASH": password_hash(admin_password),
                  "VIEWER_USERNAME": "viewer", "VIEWER_PASSWORD_HASH": password_hash(viewer_password), "INGRESSO_PARTNERSHIPS": "warner,sony"}
    if reuse:
        source = ROOT.parent / "application" / "local" / "private"
        provider_path, relay_path = source / "provider-secrets.json", source / "cinemark-relay.json"
        providers = json.loads(provider_path.read_text(encoding="utf-8-sig"))
        # Local secret-store keys can be ARNs, parameter paths, or provider names.
        for key, value in providers.items():
            label = key.lower()
            if "velox" in label:
                parsed = json.loads(value) if isinstance(value, str) else value
                values["VELOX_USERNAME"] = parsed.get("username") or parsed.get("USUARIO") or ""
                values["VELOX_PASSWORD"] = parsed.get("password") or parsed.get("SENHA") or ""
            elif "/providers/is/" in label or "international" in label or label == "is" or "is-api" in label or "is_api" in label:
                values["IS_API_KEY"] = value if isinstance(value, str) else value.get("api_key", "")
        relay = json.loads(relay_path.read_text(encoding="utf-8-sig"))
        values["CINEMARK_RELAY_URL"], values["CINEMARK_RELAY_SECRET"] = relay["url"], relay["secret"]
    if database_password:
        (private / "db-password").write_text(database_password, encoding="utf-8")
        (ROOT / ".env").write_text("DATA_CHECK_DB_PASSWORD=" + database_password + "\n", encoding="utf-8")
        (ROOT / "frontend" / ".env.local").write_text(env_text({"BACKEND_URL": "http://127.0.0.1:8001/api", "PROXY_SECRET": proxy_secret}), encoding="utf-8")
        (private / "local-access.txt").write_text(f"Local URL: http://localhost:3001\nFull access username: administrator\nFull access password: {admin_password}\n\nRefresh/view username: viewer\nRefresh/view password: {viewer_password}\n\nKeep this file private. These credentials have not been published.\n", encoding="utf-8")
    target.write_text(env_text(values), encoding="utf-8")
    print("Local configuration generated. Credentials are in private/local-access.txt; nothing sensitive was printed.")
    print("Provider credential fields configured: " + ", ".join(key for key in ("IS_API_KEY", "VELOX_USERNAME", "VELOX_PASSWORD", "CINEMARK_RELAY_URL", "CINEMARK_RELAY_SECRET") if values.get(key)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-admin-credentials", action="store_true")
    setup(parser.parse_args().reuse_admin_credentials)
