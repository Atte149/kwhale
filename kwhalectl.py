#!/usr/bin/env python3
"""KWhale control CLI — manage a running KWhale installation.

Usage:
    python3 kwhalectl.py status          # show container status + health
    python3 kwhalectl.py smoke            # end-to-end smoke test
    python3 kwhalectl.py logs [service]  # tail logs
    python3 kwhalectl.py restart [svc]    # restart service(s)
    python3 kwhalectl.py scan             # trigger Navidrome library scan
    python3 kwhalectl.py index            # trigger AI indexing
    python3 kwhalectl.py token            # get API token
    python3 kwhalectl.py stop             # stop all services
    python3 kwhalectl.py start            # start all services
    python3 kwhalectl.py update           # git pull + rebuild + restart
    python3 kwhalectl.py create-user <name>  # create a Navidrome user
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

INSTALL_DIR = Path(__file__).parent.resolve()


def _docker_needs_sudo() -> bool:
    """Detect if docker commands require sudo on this host."""
    r = subprocess.run(["docker", "info"], capture_output=True)
    return r.returncode != 0


SUDO: list[str] = ["sudo"] if _docker_needs_sudo() else []


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=INSTALL_DIR, **kw)


def compose_cmd(args: list[str], **kw) -> subprocess.CompletedProcess:
    return run(SUDO + ["docker", "compose"] + args, **kw)


def get_env() -> dict:
    """Load .env from install dir."""
    env_file = INSTALL_DIR / ".env"
    env = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def get_api_port() -> int:
    """Extract API port from compose or default."""
    override = INSTALL_DIR / "docker-compose.override.yml"
    if override.exists():
        for line in override.read_text().splitlines():
            if "19000" in line or ":8000" in line:
                for part in line.strip().strip("-").strip('"').split(":"):
                    if part.isdigit():
                        return int(part)
    return 19000


import ssl

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def http_get(url: str, timeout: int = 10, headers: dict | None = None) -> tuple[int, str]:
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode(errors="replace")
        except Exception:
            body = str(e)
        return e.code, body
    except Exception as e:
        return 0, str(e)


def http_post(url: str, data: dict, headers: dict | None = None, timeout: int = 10) -> tuple[int, str]:
    try:
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode(errors="replace")
        except Exception:
            body = str(e)
        return e.code, body
    except Exception as e:
        return 0, str(e)


def http_post_json(url: str, data: dict, headers: dict | None = None, timeout: int = 10) -> tuple[int, str]:
    """Alias for http_post — returns (status_code, body_text)."""
    return http_post(url, data, headers, timeout)


def http_put(url: str, data: dict, headers: dict | None = None, timeout: int = 10) -> tuple[int, str]:
    try:
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json", **(headers or {})},
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode(errors="replace")
        except Exception:
            body = str(e)
        return e.code, body
    except Exception as e:
        return 0, str(e)


def http_delete(url: str, headers: dict | None = None, timeout: int = 10) -> tuple[int, str]:
    try:
        req = urllib.request.Request(
            url, headers=headers or {}, method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode(errors="replace")
        except Exception:
            body = str(e)
        return e.code, body
    except Exception as e:
        return 0, str(e)


# ───────────────────────── commands ─────────────────────────


def cmd_status(args: list[str]) -> None:
    """Show container status + health endpoints."""
    env = get_env()
    print("=== Containers ===")
    compose_cmd(["ps"], text=True)
    print()

    print("=== Health ===")
    api_port = get_api_port()
    nav_port = api_port - 19000 + 4535

    api_url = f"http://127.0.0.1:{api_port}/healthz"
    nav_url = f"http://127.0.0.1:{nav_port}/rest/ping.view"

    code, _ = http_get(api_url)
    print(f"  API        {api_url}  [{'OK' if code == 200 else 'FAIL'}] ({code})")

    code, _ = http_get(nav_url)
    print(f"  Navidrome  {nav_url}  [{'OK' if code == 200 else 'FAIL'}] ({code})")

    print()
    print(f"  Data root: {env.get('DATA_ROOT', '?')}")
    print(f"  Install:   {INSTALL_DIR}")


def cmd_smoke(args: list[str]) -> None:
    """End-to-end smoke test."""
    env = get_env()
    api_port = get_api_port()
    nav_port = api_port - 19000 + 4535
    base = f"http://127.0.0.1:{api_port}"
    user = env.get("NAVIDROME_USERNAME", "admin")
    pw = env.get("NAVIDROME_PASSWORD", "")

    if not pw:
        print("  [x] No NAVIDROME_PASSWORD in .env")
        sys.exit(1)

    print("=== Smoke Test ===")
    fail = 0

    # Health
    code, _ = http_get(f"{base}/healthz")
    ok = code == 200
    print(f"  [{'OK' if ok else 'FAIL'}] /healthz ({code})")
    fail += 0 if ok else 1

    code, _ = http_get(f"http://127.0.0.1:{nav_port}/rest/ping.view")
    ok = code == 200
    print(f"  [{'OK' if ok else 'FAIL'}] Navidrome ping ({code})")
    fail += 0 if ok else 1

    # Auth
    code, body = http_post(f"{base}/api/auth/login", {"username": user, "password": pw})
    if code == 200:
        token = json.loads(body).get("token", "")
        print(f"  [OK] login (token len {len(token)})")
    else:
        print(f"  [FAIL] login ({code})")
        sys.exit(1)

    # Recommendations (needs auth)
    code, body = http_get(
        f"{base}/api/recs?type=hybrid&limit=5",
        headers={"Authorization": f"Bearer {token}"},
    )
    if code == 200:
        try:
            tracks = json.loads(body).get("tracks", [])
            print(f"  [OK] /api/recs hybrid ({len(tracks)} tracks)")
        except Exception:
            print(f"  [FAIL] /api/recs parse")
            fail += 1
    else:
        print(f"  [FAIL] /api/recs ({code})")
        fail += 1

    # Stream URL contract
    if code == 200:
        try:
            tracks = json.loads(body).get("tracks", [])
            if tracks:
                su = tracks[0].get("streamUrl", "")
                cu = tracks[0].get("coverUrl", "")
                if su:
                    code2, _ = http_get(su)
                    print(f"  [{'OK' if code2 in (200, 206) else 'FAIL'}] streamUrl ({code2})")
                    fail += 0 if code2 in (200, 206) else 1
                if cu:
                    code3, _ = http_get(cu)
                    print(f"  [{'OK' if code3 == 200 else 'FAIL'}] coverUrl ({code3})")
                    fail += 0 if code3 == 200 else 1
        except Exception as e:
            print(f"  [FAIL] stream/cover check: {e}")
            fail += 1

    print()
    print(f"SMOKE: {'PASS' if fail == 0 else 'FAIL'}")
    sys.exit(fail)


def cmd_logs(args: list[str]) -> None:
    """Tail logs."""
    service = args[0] if args else ""
    cmd = ["logs", "-f", "--tail", "100"]
    if service:
        cmd.append(service)
    compose_cmd(cmd)


def cmd_restart(args: list[str]) -> None:
    """Restart service(s)."""
    if args:
        compose_cmd(["restart"] + args)
    else:
        compose_cmd(["restart"])


def cmd_stop(args: list[str]) -> None:
    compose_cmd(["down"])


def cmd_start(args: list[str]) -> None:
    compose_cmd(["up", "-d"])


def cmd_update(args: list[str]) -> None:
    """Git pull + rebuild + restart."""
    print("  [+] Pulling latest code...")
    run(["git", "pull", "origin", "main"])
    print("  [+] Rebuilding images...")
    compose_cmd(["build"])
    print("  [+] Restarting services...")
    compose_cmd(["up", "-d", "--force-recreate"])
    print("  [+] Done.")


def cmd_scan(args: list[str]) -> None:
    """Trigger Navidrome scan via Subsonic API."""
    env = get_env()
    nav_port = get_api_port() - 19000 + 4535
    user = env.get("NAVIDROME_USERNAME", "admin")
    pw = env.get("NAVIDROME_PASSWORD", "")

    # Subsonic scan start
    import hashlib, time
    salt = str(time.time())
    token = hashlib.md5((pw + salt).encode()).hexdigest()
    url = f"http://127.0.0.1:{nav_port}/rest/startScan?u={user}&t={token}&s={salt}&f=json"
    code, body = http_get(url)
    if code == 200:
        print("  [OK] Navidrome scan started")
    else:
        print(f"  [FAIL] Scan trigger ({code}): {body}")


def cmd_index(args: list[str]) -> None:
    """Trigger AI indexing."""
    env = get_env()
    api_port = get_api_port()
    user = env.get("NAVIDROME_USERNAME", "admin")
    pw = env.get("NAVIDROME_PASSWORD", "")

    code, body = http_post(f"http://127.0.0.1:{api_port}/api/auth/login", {"username": user, "password": pw})
    if code != 200:
        print(f"  [FAIL] Login ({code})")
        sys.exit(1)
    token = json.loads(body).get("token", "")

    code, body = http_post(
        f"http://127.0.0.1:{api_port}/api/vibe/index-all",
        {}, {"Authorization": f"Bearer {token}"}
    )
    if code == 202:
        print("  [OK] Indexing started. Check: docker compose logs -f worker")
    else:
        print(f"  [FAIL] Index trigger ({code}): {body}")


def cmd_token(args: list[str]) -> None:
    """Get API token."""
    env = get_env()
    api_port = get_api_port()
    user = env.get("NAVIDROME_USERNAME", "admin")
    pw = env.get("NAVIDROME_PASSWORD", "")

    code, body = http_post(f"http://127.0.0.1:{api_port}/api/auth/login", {"username": user, "password": pw})
    if code == 200:
        token = json.loads(body).get("token", "")
        print(token)
    else:
        print(f"  [FAIL] Login ({code}): {body}", file=sys.stderr)
        sys.exit(1)


def cmd_create_user(args: list[str]) -> None:
    """Create a Navidrome user via Native REST API.

    Usage: kwhalectl create-user <username> [password]
    """
    if not args:
        print("  Usage: kwhalectl create-user <username> [password]")
        sys.exit(1)

    username = args[0]
    env = get_env()
    nav_port = get_api_port() - 19000 + 4535
    admin = env.get("NAVIDROME_USERNAME", "admin")
    pw = env.get("NAVIDROME_PASSWORD", "")

    import getpass
    password = args[1] if len(args) > 1 else getpass.getpass("Password: ")

    # Step 1: Get JWT token from Navidrome
    code, body = http_post_json(
        f"http://127.0.0.1:{nav_port}/auth/login",
        {"username": admin, "password": pw},
    )
    if code != 200:
        print(f"  [FAIL] Admin login ({code}): {body}")
        sys.exit(1)
    jwt = json.loads(body).get("token", "")

    # Step 2: Create user via Native REST API
    code, body = http_post(
        f"http://127.0.0.1:{nav_port}/api/user",
        {"userName": username, "password": password, "name": username, "isAdmin": False},
        headers={"x-nd-authorization": f"Bearer {jwt}"},
    )
    if code == 200:
        user_id = ""
        try:
            user_id = json.loads(body).get("id", "")
        except Exception:
            pass
        print(f"  [OK] User '{username}' created (id: {user_id})")
    else:
        print(f"  [FAIL] CreateUser ({code}): {body}")


def _nav_login(nav_port: int, admin: str, pw: str) -> str:
    """Get Navidrome JWT token."""
    code, body = http_post_json(
        f"http://127.0.0.1:{nav_port}/auth/login",
        {"username": admin, "password": pw},
    )
    if code != 200:
        print(f"  [FAIL] Admin login ({code}): {body}")
        sys.exit(1)
    return json.loads(body).get("token", "")


def cmd_create_client(args: list[str]) -> None:
    """Create an isolated client: user + music folder + library + access.

    Usage: kwhalectl create-client <username> [password]

    Creates:
      1. Data folder: $DATA_ROOT/music/library/<username>/
      2. Navidrome user (non-admin)
      3. Navidrome library pointing to the user's folder
      4. Restricts user access to ONLY their library (removes default library)
    """
    if not args:
        print("  Usage: kwhalectl create-client <username> [password]")
        sys.exit(1)

    username = args[0]
    env = get_env()
    nav_port = get_api_port() - 19000 + 4535
    admin = env.get("NAVIDROME_USERNAME", "admin")
    pw = env.get("NAVIDROME_PASSWORD", "")
    data_root = env.get("DATA_ROOT", "")

    import getpass
    password = args[1] if len(args) > 1 else getpass.getpass("Password: ")

    print(f"\n  Creating isolated client '{username}'...")

    # 1. Create music folder
    music_dir = f"{data_root}/music/library/{username}"
    os.makedirs(music_dir, exist_ok=True)
    print(f"  [+] Music folder: {music_dir}")

    # 2. Get admin JWT
    jwt = _nav_login(nav_port, admin, pw)

    # 3. Create Navidrome library
    lib_name = f"{username}'s Library"
    # Container path: /music/<username> (Navidrome mounts DATA_ROOT/music/library as /music)
    container_path = f"/music/{username}"
    code, body = http_post(
        f"http://127.0.0.1:{nav_port}/api/library",
        {"name": lib_name, "path": container_path, "defaultNewUsers": False},
        headers={"x-nd-authorization": f"Bearer {jwt}"},
    )
    if code != 200:
        print(f"  [FAIL] Create library ({code}): {body}")
        sys.exit(1)
    lib_id = ""
    try:
        lib_id = json.loads(body).get("id", "")
    except Exception:
        pass
    # Navidrome expects libraryIds as []int
    try:
        lib_id_int = int(lib_id)
    except (ValueError, TypeError):
        lib_id_int = lib_id
    print(f"  [+] Library '{lib_name}' created (id: {lib_id})")

    # 4. Create user
    code, body = http_post(
        f"http://127.0.0.1:{nav_port}/api/user",
        {"userName": username, "password": password, "name": username, "isAdmin": False},
        headers={"x-nd-authorization": f"Bearer {jwt}"},
    )
    if code != 200:
        print(f"  [FAIL] Create user ({code}): {body}")
        sys.exit(1)
    user_id = ""
    try:
        user_id = json.loads(body).get("id", "")
    except Exception:
        pass
    print(f"  [+] User '{username}' created (id: {user_id})")

    # 5. Restrict user access to ONLY the new library (remove default library)
    code, body = http_put(
        f"http://127.0.0.1:{nav_port}/api/user/{user_id}/library",
        {"libraryIds": [lib_id_int]},
        headers={"x-nd-authorization": f"Bearer {jwt}"},
    )
    if code == 200:
        print(f"  [+] User access restricted to '{lib_name}' only")
    else:
        print(f"  [WARN] Could not restrict library access ({code}): {body}")
        print(f"         Manually edit user in Navidrome UI to remove default library")

    # 7. Trigger scan of the new library
    import hashlib, time, secrets
    salt = secrets.token_hex(6)
    token = hashlib.md5(f"{pw}{salt}".encode()).hexdigest()
    code, _ = http_get(
        f"http://127.0.0.1:{nav_port}/rest/startScan.view",
        headers={},
    )
    # Use the simple startScan
    try:
        scan_url = (f"http://127.0.0.1:{nav_port}/rest/startScan.view"
                    f"?u={admin}&t={token}&s={salt}&v=1.16.1&c=kwhalectl&f=json")
        http_get(scan_url)
    except Exception:
        pass

    print(f"\n  [OK] Client '{username}' ready!")
    print(f"       Music folder: {music_dir}")
    print(f"       Library:      {lib_name}")
    print(f"       Login:        {username} / (password you set)")
    print(f"\n  Next: copy music to {music_dir}/")
    print(f"        Navidrome will auto-scan within 1 minute.")


def cmd_list_libraries(args: list[str]) -> None:
    """List all Navidrome libraries and their users."""
    env = get_env()
    nav_port = get_api_port() - 19000 + 4535
    admin = env.get("NAVIDROME_USERNAME", "admin")
    pw = env.get("NAVIDROME_PASSWORD", "")

    jwt = _nav_login(nav_port, admin, pw)

    # Get libraries
    code, body = http_get(
        f"http://127.0.0.1:{nav_port}/api/library",
        headers={"x-nd-authorization": f"Bearer {jwt}"},
    )
    if code != 200:
        print(f"  [FAIL] Get libraries ({code}): {body}")
        sys.exit(1)
    libs = json.loads(body)

    # Get users
    code, body = http_get(
        f"http://127.0.0.1:{nav_port}/api/user",
        headers={"x-nd-authorization": f"Bearer {jwt}"},
    )
    users = json.loads(body) if code == 200 else []

    print("=== Libraries ===")
    for lib in libs:
        n_songs = lib.get("totalSongs", 0)
        n_albums = lib.get("totalAlbums", 0)
        default = " (default)" if lib.get("defaultNewUsers") else ""
        print(f"  [{lib['id']}] {lib['name']}{default}")
        print(f"      Path:   {lib['path']}")
        print(f"      Songs:  {n_songs}, Albums: {n_albums}")
        # Find users with access
        lib_users = [u["userName"] for u in users
                     if any(l["id"] == lib["id"] for l in u.get("libraries", []))]
        print(f"      Users:  {', '.join(lib_users) if lib_users else '(none)'}")
        print()


COMMANDS = {
    "status": cmd_status,
    "smoke": cmd_smoke,
    "logs": cmd_logs,
    "restart": cmd_restart,
    "stop": cmd_stop,
    "start": cmd_start,
    "update": cmd_update,
    "scan": cmd_scan,
    "index": cmd_index,
    "token": cmd_token,
    "create-user": cmd_create_user,
    "create-client": cmd_create_client,
    "list-libraries": cmd_list_libraries,
}


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}\n")
        print(__doc__)
        sys.exit(1)

    COMMANDS[cmd](args)


if __name__ == "__main__":
    main()