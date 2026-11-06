"""Load config/secrets: .env sets defaults, then OpenBao AppRole overrides
them if configured. Mirrors household-manager's secrets.py pattern exactly
(same fail-open-to-.env behavior, same FATAL:/WARN:/INFO: stderr tags)."""
import os
import sys
from pathlib import Path

import httpx

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _load_env() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _bao_login(addr: str, role_id: str, secret_id: str) -> str:
    resp = httpx.post(
        f"{addr}/v1/auth/approle/login",
        json={"role_id": role_id, "secret_id": secret_id},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["auth"]["client_token"]


def _bao_fetch(addr: str, token: str, path: str, field_to_env: dict) -> None:
    resp = httpx.get(
        f"{addr}/v1/secret/data/{path}",
        headers={"X-Vault-Token": token},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()["data"]["data"]
    for field, env_name in field_to_env.items():
        if field in data:
            os.environ[env_name] = data[field]


def _fetch_from_openbao() -> None:
    addr = os.environ.get("BAO_ADDR")
    role_id = os.environ.get("BAO_ROLE_ID")
    secret_id_file = os.environ.get("BAO_SECRET_ID_FILE")
    if not (addr and role_id and secret_id_file):
        return
    try:
        secret_id = Path(secret_id_file).read_text(encoding="utf-8").strip()
        token = _bao_login(addr, role_id, secret_id)
        _bao_fetch(addr, token, "sim-league-bot/discord", {"token": "DISCORD_BOT_TOKEN"})
        _bao_fetch(addr, token, "sim-league-bot/anthropic", {"api_key": "ANTHROPIC_API_KEY"})
        print("INFO: secrets fetched from OpenBao via AppRole", file=sys.stderr)
    except Exception as exc:
        print(f"WARN: OpenBao login/fetch failed, falling back to .env: {exc}", file=sys.stderr)


def require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"FATAL: missing required config {name}", file=sys.stderr)
        sys.exit(1)
    return value


def load() -> None:
    _load_env()
    _fetch_from_openbao()
