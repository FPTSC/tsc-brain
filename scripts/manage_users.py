#!/usr/bin/env python3
"""Gestione utenti TSC-Brain (CLI alternativa al pannello web /admin).

Comandi:
  python scripts/manage_users.py add <username> <password> [--admin]
  python scripts/manage_users.py remove <username>
  python scripts/manage_users.py list
"""
import json
import sys
from pathlib import Path

import bcrypt

USERS_FILE = Path(__file__).parent.parent / "users.json"


def _load() -> dict:
    if USERS_FILE.exists():
        raw = json.loads(USERS_FILE.read_text())
        return {k: v if isinstance(v, dict) else {"hash": v, "admin": False} for k, v in raw.items()}
    return {}


def _save(users: dict) -> None:
    USERS_FILE.write_text(json.dumps(users, indent=2, ensure_ascii=False))


def cmd_add(username: str, password: str, is_admin: bool) -> None:
    users = _load()
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    users[username] = {"hash": hashed, "admin": is_admin}
    _save(users)
    role = "admin" if is_admin else "utente"
    print(f"Utente '{username}' aggiunto come {role}.")


def cmd_remove(username: str) -> None:
    users = _load()
    if username not in users:
        print(f"Utente '{username}' non trovato.")
        sys.exit(1)
    del users[username]
    _save(users)
    print(f"Utente '{username}' rimosso.")


def cmd_list() -> None:
    users = _load()
    if not users:
        print("Nessun utente configurato.")
        return
    print(f"{len(users)} utente/i:")
    for u, entry in users.items():
        role = " [admin]" if entry.get("admin") else ""
        print(f"  - {u}{role}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    cmd = args[0]
    if cmd == "add" and len(args) >= 3:
        cmd_add(args[1], args[2], "--admin" in args)
    elif cmd == "remove" and len(args) == 2:
        cmd_remove(args[1])
    elif cmd == "list":
        cmd_list()
    else:
        print(__doc__)
        sys.exit(1)
