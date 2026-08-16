#!/usr/bin/env python3
"""Dashboard web do Brain (spec §6.3) — FastAPI + UI simples.

Login admin-only (usuário/senha), upload de arquivos OU caminho no servidor,
seleção de expert/global, execução de `learn` com opção de sync e visualização
de jobs/status e integridade (check).

Ativação:
    brain dashboard                                # sobe o servidor
    brain dashboard add-user <usuario> <senha>     # cria credencial admin
    brain dashboard list-users
    brain dashboard remove-user <usuario>

Também disponível como console script: `brain-dashboard`.

Segurança (spec §5.2/§7): a absorção de conhecimento é operação administrativa.
Toda rota mutável exige sessão autenticada; credenciais ficam em
`admins.json` (campo `dashboard_users`), com senha em PBKDF2-SHA256.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .brain_tool import check, count_pages, learn, list_jobs
from .db import get_brain_root, get_session, validate_expert_identifier

__all__ = [
    "add_dashboard_user", "list_dashboard_users", "remove_dashboard_user",
    "authenticate", "create_app", "serve", "main",
]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8611

_PBKDF2_ITERATIONS = 100_000
_COOKIE = "brain_session"
_SESSION_TTL = 8 * 3600  # 8 horas
_LOGIN_MAX_FAILURES = 5
_LOGIN_LOCKOUT_SECONDS = 300
MAX_UPLOAD_BYTES = int(os.environ.get("BRAIN_DASHBOARD_MAX_UPLOAD", str(50 * 1024 * 1024)))


# --- Credenciais de administradores (admins.json) ---------------------------

def _admins_file() -> Path:
    return get_brain_root() / "admins.json"


def _load_admins() -> dict:
    path = _admins_file()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_admins(data: dict) -> None:
    path = _admins_file()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.chmod(path, 0o600)


def _hash_password(password: str, *, salt: Optional[bytes] = None) -> str:
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iters)
        )
        return hmac.compare_digest(digest, expected)
    except Exception:
        return False


def add_dashboard_user(username: str, password: str) -> None:
    """Cria/atualiza um usuário do dashboard (também vira admin `cli:`)."""
    username = (username or "").strip()
    if not username or not password:
        raise ValueError("usuario e senha sao obrigatorios")
    data = _load_admins()
    users = data.setdefault("dashboard_users", {})
    users[username] = _hash_password(password)
    admins = data.setdefault("admins", [])
    key = f"cli:{username}"
    if key not in admins:
        admins.append(key)
    _save_admins(data)


def list_dashboard_users() -> List[str]:
    return sorted(_load_admins().get("dashboard_users", {}).keys())


def remove_dashboard_user(username: str) -> bool:
    data = _load_admins()
    users = data.get("dashboard_users", {})
    if username not in users:
        return False
    del users[username]
    key = f"cli:{username}"
    if key in data.get("admins", []):
        data["admins"].remove(key)
    _save_admins(data)
    return True


def authenticate(username: str, password: str) -> bool:
    users = _load_admins().get("dashboard_users", {})
    stored = users.get(username)
    if not stored:
        # Gasta CPU equivalente a uma verificação real para não vazar (timing)
        # quais usernames existem.
        _hash_password(password)
        return False
    return _verify_password(password, stored)


# --- Sessão assinada (cookie stateless) -------------------------------------

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def _make_token(secret: str, username: str) -> str:
    payload = _b64(json.dumps(
        {"u": username, "exp": int(time.time()) + _SESSION_TTL}
    ).encode("utf-8"))
    return f"{payload}.{_sign(secret, payload)}"


def _verify_token(secret: str, token: str) -> Optional[str]:
    try:
        payload, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, _sign(secret, payload)):
            return None
        data = json.loads(_b64d(payload))
        if int(data.get("exp", 0)) < time.time():
            return None
        return data.get("u")
    except Exception:
        return None


# --- Secret persistente + rate limiting + audit -----------------------------

def _load_or_create_secret() -> str:
    """Secret estável da sessão: env, ou arquivo 0600 persistido no brain root.

    Um secret persistente (em vez de por-processo) é necessário para que a
    sessão sobreviva a restarts e funcione com múltiplos workers/reverse proxy.
    """
    env = os.environ.get("BRAIN_DASHBOARD_SECRET")
    if env:
        return env
    path = get_brain_root() / ".dashboard_secret"
    if path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    generated = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(generated, encoding="utf-8")
    os.chmod(path, 0o600)
    return generated


def _load_or_create_token() -> str:
    """Token de acesso (auth single-admin) — env ou `.dashboard_token` 0600."""
    env = os.environ.get("BRAIN_DASHBOARD_TOKEN")
    if env:
        return env
    path = get_brain_root() / ".dashboard_token"
    if path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    generated = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(generated, encoding="utf-8")
    os.chmod(path, 0o600)
    return generated


def get_access_token() -> str:
    """Retorna o token de acesso persistente (CLI `brain dashboard token`)."""
    return _load_or_create_token()


def _lan_ip() -> Optional[str]:
    """IP da máquina na LAN (best-effort, só para dica de URL)."""
    try:
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


class _LoginRateLimiter:
    """Lockout em memória por usuário (anti brute-force)."""

    def __init__(self, max_failures: int, lockout_seconds: int):
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self._failures: dict = {}
        self._locks: dict = {}
        self._lock = threading.Lock()

    def is_locked(self, username: str) -> bool:
        now = time.time()
        with self._lock:
            until = self._locks.get(username)
            if until and until > now:
                return True
            if until:
                self._locks.pop(username, None)
            return False

    def record_failure(self, username: str) -> None:
        now = time.time()
        with self._lock:
            recent = [t for t in self._failures.get(username, [])
                      if now - t < self.lockout_seconds]
            recent.append(now)
            if len(recent) >= self.max_failures:
                self._locks[username] = now + self.lockout_seconds
                self._failures[username] = []
            else:
                self._failures[username] = recent

    def record_success(self, username: str) -> None:
        with self._lock:
            self._failures.pop(username, None)
            self._locks.pop(username, None)


def _audit(event: str, **fields) -> None:
    """Registra evento em audit.log (best-effort; nunca derruba o fluxo)."""
    try:
        path = get_brain_root() / "audit.log"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        entry = {"ts": datetime.utcnow().isoformat() + "Z", "event": event, **fields}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        os.chmod(path, 0o600)
    except Exception:
        pass


# --- Status dos serviços (Redis / Celery / Docker) --------------------------

def _redis_status() -> dict:
    url = (os.environ.get("REDIS_URL")
           or os.environ.get("CELERY_BROKER_URL")
           or "redis://localhost:6379/0")
    try:
        import redis

        r = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        r.ping()
        return {"ok": True, "url": url, "detail": "pong"}
    except Exception as e:  # noqa: BLE001 — status é best-effort
        return {"ok": False, "url": url, "detail": str(e)}


def _celery_status() -> dict:
    broker = (os.environ.get("CELERY_BROKER_URL")
              or os.environ.get("REDIS_URL")
              or "redis://localhost:6379/0")
    try:
        from brain_tool.worker import app as celery_app

        ping = celery_app.control.inspect(timeout=2).ping()
        workers = sorted(ping.keys()) if ping else []
        if workers:
            return {"ok": True, "broker": broker, "workers": workers,
                    "detail": f"{len(workers)} worker(s): {', '.join(workers)}"}
        return {"ok": True, "broker": broker, "workers": [],
                "detail": "broker ok, nenhum worker rodando"}
    except Exception as e:  # noqa: BLE001 — status é best-effort
        return {"ok": False, "broker": broker, "detail": str(e)}


def _docker_status() -> dict:
    try:
        info = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=5)
        if info.returncode != 0:
            return {"ok": False, "detail": (info.stderr or "docker indisponível").strip()[:300]}
        ps = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=5,
        )
        containers = []
        for line in ps.stdout.strip().splitlines():
            if line.strip():
                name, sep, status = line.partition("\t")
                containers.append({"name": name, "status": status})
        return {"ok": True, "containers": containers,
                "detail": f"{len(containers)} contêiner(es) rodando"}
    except FileNotFoundError:
        return {"ok": False, "detail": "docker CLI não encontrado"}
    except Exception as e:  # noqa: BLE001 — status é best-effort
        return {"ok": False, "detail": str(e)}


# --- Helpers de domínio -----------------------------------------------------

def _expert_names() -> List[str]:
    experts_dir = get_brain_root() / "experts"
    if not experts_dir.is_dir():
        return []
    return sorted(d.name for d in experts_dir.iterdir() if d.is_dir())


def _uploads_dir() -> Path:
    d = get_brain_root() / ".uploads"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


# --- App --------------------------------------------------------------------

def create_app(secret: Optional[str] = None, token: Optional[str] = None) -> FastAPI:
    secret = secret or _load_or_create_secret()
    access_token = token or _load_or_create_token()
    secure_cookies = os.environ.get("BRAIN_DASHBOARD_SECURE_COOKIES", "") in ("1", "true", "yes")
    limiter = _LoginRateLimiter(_LOGIN_MAX_FAILURES, _LOGIN_LOCKOUT_SECONDS)

    app = FastAPI(title="Brain Dashboard", docs_url=None, redoc_url=None)

    def current_user(request: Request) -> str:
        # 1) sessão por cookie (login por senha OU troca de token)
        cookie = request.cookies.get(_COOKIE)
        user = _verify_token(secret, cookie) if cookie else None
        if user:
            return user
        # 2) token bearer (Authorization: Bearer <token>)
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer ") and hmac.compare_digest(auth[7:].strip(), access_token):
            return "admin"
        raise HTTPException(status_code=401, detail="nao autenticado")

    def same_origin(request: Request) -> None:
        """Bloqueia POST cross-origin (defesa CSRF em profundidade).

        Clientes sem header Origin (curl/CLI) são permitidos; quando o header
        existe, o host de origem precisa bater com o host da requisição.
        """
        origin = request.headers.get("origin")
        if not origin:
            return
        try:
            o = urlparse(origin)
            if o.hostname != request.url.hostname:
                raise HTTPException(status_code=403, detail="origem nao permitida")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=403, detail="origem nao permitida")

    @app.get("/", response_class=HTMLResponse)
    def index(token: Optional[str] = None):
        if token:
            if hmac.compare_digest(token, access_token):
                _audit("token_login")
                resp = RedirectResponse("/", status_code=302)
                resp.set_cookie(_COOKIE, _make_token(secret, "admin"),
                                httponly=True, samesite="strict", secure=secure_cookies,
                                max_age=_SESSION_TTL)
                return resp
            return RedirectResponse("/", status_code=302)
        return HTMLResponse(_INDEX_HTML)

    @app.post("/login")
    async def login(
        username: str = Form(...),
        password: str = Form(...),
        _origin: None = Depends(same_origin),
    ):
        if limiter.is_locked(username):
            _audit("login_locked", user=username)
            return JSONResponse(
                {"ok": False, "error": "muitas tentativas; aguarde antes de tentar novamente"},
                status_code=429,
            )
        if not authenticate(username, password):
            limiter.record_failure(username)
            _audit("login_failure", user=username)
            return JSONResponse({"ok": False, "error": "credenciais invalidas"},
                                status_code=401)
        limiter.record_success(username)
        _audit("login_success", user=username)
        resp = JSONResponse({"ok": True, "user": username})
        resp.set_cookie(_COOKIE, _make_token(secret, username),
                        httponly=True, samesite="strict", secure=secure_cookies,
                        max_age=_SESSION_TTL)
        return resp

    @app.post("/login-token")
    async def login_token(token: str = Form(...), _origin: None = Depends(same_origin)):
        if not hmac.compare_digest(token, access_token):
            _audit("token_login_failure")
            return JSONResponse({"ok": False, "error": "token invalido"}, status_code=401)
        _audit("token_login")
        resp = JSONResponse({"ok": True, "user": "admin"})
        resp.set_cookie(_COOKIE, _make_token(secret, "admin"),
                        httponly=True, samesite="strict", secure=secure_cookies,
                        max_age=_SESSION_TTL)
        return resp

    @app.get("/logout")
    def logout() -> RedirectResponse:
        resp = RedirectResponse("/")
        resp.delete_cookie(_COOKIE)
        return resp

    @app.get("/api/me")
    def me(user: str = Depends(current_user)):
        return {"user": user}

    @app.get("/api/experts")
    def experts(user: str = Depends(current_user)):
        result = [{"name": "global", "global": True, "knowledge": None}]
        for name in _expert_names():
            count = 0
            try:
                conn = get_session(expert=name)
                count = count_pages(conn, name)
                conn.close()
            except Exception:
                count = 0
            result.append({"name": name, "global": False, "knowledge": count})
        return {"experts": result}

    @app.post("/api/learn")
    async def api_learn(
        _origin: None = Depends(same_origin),
        user: str = Depends(current_user),
        expert: str = Form(...),
        global_brain: bool = Form(False),
        sync_flag: bool = Form(False),
        path: str = Form(""),
        url: str = Form(""),
        files: Optional[List[UploadFile]] = File(default=None),
    ):
        if global_brain:
            target = "global"
        else:
            try:
                target = validate_expert_identifier(expert)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))

        path = (path or "").strip()
        url = (url or "").strip()
        uploads = [f for f in (files or []) if f.filename]
        if not path and not url and not uploads:
            raise HTTPException(
                status_code=400, detail="informe um caminho, uma URL ou envie arquivos"
            )

        conn = get_session(expert=target, global_brain=global_brain)
        try:
            results = []
            if path:
                results.append(learn(conn, target, path, sync_immediately=sync_flag))
            if url:
                try:
                    results.append(learn(conn, target, url, sync_immediately=sync_flag))
                except ValueError as e:
                    raise HTTPException(status_code=400, detail=str(e))
            for f in uploads:
                safe_name = Path(f.filename or "upload").name
                dest = _uploads_dir() / f"{uuid.uuid4().hex}_{safe_name}"
                content = await f.read(MAX_UPLOAD_BYTES + 1)
                if len(content) > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"arquivo excede o limite de {MAX_UPLOAD_BYTES // (1024 * 1024)}MB",
                    )
                dest.write_bytes(content)
                os.chmod(dest, 0o600)
                results.append(learn(conn, target, str(dest), sync_immediately=sync_flag))
            _audit("learn", user=user, target=target, sync=sync_flag,
                   path=path or None, url=url or None, files=[f.filename for f in uploads])
            return {"ok": True, "target": target, "results": results}
        finally:
            conn.close()

    @app.get("/api/jobs")
    def api_jobs(
        expert: str,
        global_brain: bool = False,
        status: str = "",
        limit: int = 50,
        user: str = Depends(current_user),
    ):
        target = "global" if global_brain else expert
        conn = get_session(expert=target, global_brain=global_brain)
        try:
            return {"jobs": list_jobs(conn, target, status or None, limit)}
        finally:
            conn.close()

    @app.get("/api/check")
    def api_check(
        expert: str,
        global_brain: bool = False,
        user: str = Depends(current_user),
    ):
        target = "global" if global_brain else expert
        conn = get_session(expert=target, global_brain=global_brain)
        try:
            return check(conn, target)
        finally:
            conn.close()

    @app.get("/api/status")
    def api_status(user: str = Depends(current_user)):
        return {
            "redis": _redis_status(),
            "celery": _celery_status(),
            "docker": _docker_status(),
        }

    return app


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> int:
    import uvicorn

    access_token = _load_or_create_token()
    print("\n=== Brain Dashboard ===")
    print(f"  Token de acesso: {access_token}")
    print(f"  Local:  http://127.0.0.1:{port}/?token={access_token}")
    if host not in ("127.0.0.1", "localhost", "::1"):
        lan = _lan_ip()
        if lan:
            print(f"  LAN:    http://{lan}:{port}/?token={access_token}")
    print("  (login por usuário/senha continua disponível, se configurado)")
    print()
    uvicorn.run(create_app(), host=host, port=port, log_level="info")
    return 0


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="brain-dashboard")
    parser.add_argument("--host", default=os.environ.get("BRAIN_DASHBOARD_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("BRAIN_DASHBOARD_PORT", str(DEFAULT_PORT))))
    args = parser.parse_args()
    return serve(host=args.host, port=args.port)


# --- UI (HTML autocontido) --------------------------------------------------

_INDEX_HTML = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Brain Dashboard</title>
<style>
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
       background: #0f1115; color: #e6e8eb; }
#login-view { max-width: 360px; margin: 12vh auto 0; padding: 24px; }
#login-view h1 { font-size: 1.4rem; margin: 0 0 16px; }
input, select, button { font-size: 0.95rem; padding: 8px 10px; border-radius: 6px;
       border: 1px solid #2a2e37; background: #181b22; color: #e6e8eb; width: 100%; }
button { cursor: pointer; background: #2f6feb; border-color: #2f6feb; font-weight: 600; }
button:hover { background: #3b7cff; }
button.secondary { background: #22262f; border-color: #2a2e37; }
header { display: flex; align-items: center; gap: 12px; padding: 12px 20px;
         border-bottom: 1px solid #22262f; }
header h1 { font-size: 1.1rem; margin: 0; flex: 1; }
main { padding: 20px; max-width: 960px; margin: 0 auto; }
section { background: #14171d; border: 1px solid #22262f; border-radius: 10px;
          padding: 16px; margin-bottom: 16px; }
section h2 { margin: 0 0 12px; font-size: 1rem; color: #9aa4b2; }
.row { display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-end; }
.row > * { flex: 1 1 200px; }
label { display: block; font-size: 0.8rem; color: #9aa4b2; margin: 8px 0 4px; }
table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #22262f; }
th { color: #9aa4b2; font-weight: 600; }
.status-enqueued { color: #f0b429; }
.status-processing { color: #58a6ff; }
.status-completed { color: #3fb950; }
.status-failed { color: #f85149; }
pre { background: #0c0e12; padding: 10px; border-radius: 6px; overflow: auto;
      font-size: 0.8rem; max-height: 240px; }
.hidden { display: none; }
.muted { color: #6e7681; font-size: 0.8rem; }
#login-error { color: #f85149; margin-top: 8px; }
</style>
</head>
<body>
<div id="login-view">
  <form id="login-form">
    <h1>Brain Dashboard</h1>
    <label>Usuário</label>
    <input name="username" autocomplete="username" required>
    <label>Senha</label>
    <input name="password" type="password" autocomplete="current-password" required>
    <p id="login-error"></p>
    <button type="submit">Entrar</button>
    <hr style="margin:16px 0;border-color:#22262f">
    <p class="muted" style="margin:0 0 8px">Ou use o token de acesso (<code>brain dashboard token</code>):</p>
    <label>Token</label>
    <input id="token-input" placeholder="cole o token aqui">
    <button type="button" id="token-login-btn" style="margin-top:8px">Entrar com token</button>
  </form>
</div>

<div id="app-view" class="hidden">
  <header>
    <h1>Brain Dashboard</h1>
    <span id="who" class="muted"></span>
    <button class="secondary" id="logout" style="width:auto">Sair</button>
  </header>
  <main>
    <section>
      <h2>Aprender (learn)</h2>
      <div class="row">
        <div>
          <label>Alvo</label>
          <select id="target"></select>
        </div>
        <div>
          <label>Modo</label>
          <select id="mode">
            <option value="upload">Upload de arquivos</option>
            <option value="path">Caminho no servidor</option>
            <option value="url">URL</option>
          </select>
        </div>
      </div>
      <div id="upload-box">
        <label>Arquivos (.txt .md .pdf .docx .xlsx .csv)</label>
        <input type="file" id="files" multiple>
      </div>
      <div id="path-box" class="hidden">
        <label>Caminho no servidor (arquivo ou diretório)</label>
        <input type="text" id="path" placeholder="/caminho/absoluto">
      </div>
      <div id="url-box" class="hidden">
        <label>URL (http/https)</label>
        <input type="text" id="url" placeholder="https://exemplo.com/doc">
      </div>
      <label style="margin-top:10px">
        <input type="checkbox" id="sync" checked style="width:auto"> Sincronizar imediatamente (sync)
      </label>
      <button id="learn-btn" style="margin-top:12px">Executar learn</button>
      <pre id="learn-result" class="hidden"></pre>
    </section>

    <section>
      <h2>Jobs</h2>
      <div class="row">
        <button class="secondary" id="refresh-jobs" style="width:auto">Atualizar</button>
        <span class="muted" id="jobs-meta"></span>
      </div>
      <table id="jobs-table">
        <thead><tr>
          <th>ID</th><th>Comando</th><th>Status</th><th>Criado</th>
          <th>Concluído</th><th>Erro</th>
        </tr></thead>
        <tbody><tr><td colspan="6" class="muted">Nenhum job.</td></tr></tbody>
      </table>
    </section>

    <section>
      <h2>Integridade (check)</h2>
      <button class="secondary" id="check-btn" style="width:auto">Verificar</button>
      <pre id="check-result" class="hidden"></pre>
    </section>

    <section>
      <h2>Status dos serviços</h2>
      <div class="row">
        <button class="secondary" id="refresh-status" style="width:auto">Atualizar</button>
        <span class="muted" id="status-meta"></span>
      </div>
      <div id="status-box"><span class="muted">Carregando…</span></div>
    </section>
  </main>
</div>

<script>
const $ = (id) => document.getElementById(id);

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (r.status === 401) { showLogin(); throw new Error("nao autenticado"); }
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail || data.error || ("HTTP " + r.status));
  return data;
}

function showLogin() { $("login-view").classList.remove("hidden"); $("app-view").classList.add("hidden"); }
function showApp() { $("login-view").classList.add("hidden"); $("app-view").classList.remove("hidden"); }

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  try {
    const r = await fetch("/login", { method: "POST", body: fd });
    const data = await r.json();
    if (r.ok) { await boot(); }
    else { $("login-error").textContent = data.error || "Login falhou"; }
  } catch (err) { $("login-error").textContent = "Erro de conexão"; }
});

$("token-login-btn").addEventListener("click", async () => {
  const token = $("token-input").value.trim();
  if (!token) return;
  try {
    const r = await fetch("/login-token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ token }),
    });
    if (r.ok) { await boot(); }
    else {
      const data = await r.json().catch(() => ({}));
      $("login-error").textContent = data.error || data.detail || "Token inválido";
    }
  } catch (err) { $("login-error").textContent = "Erro de conexão"; }
});

$("logout").addEventListener("click", () => { location.href = "/logout"; });

$("mode").addEventListener("change", () => {
  const m = $("mode").value;
  $("upload-box").classList.toggle("hidden", m !== "upload");
  $("path-box").classList.toggle("hidden", m !== "path");
  $("url-box").classList.toggle("hidden", m !== "url");
});

function currentTarget() {
  const v = $("target").value;
  return v === "global" ? { expert: "global", global: true } : { expert: v, global: false };
}

async function loadExperts() {
  const data = await api("/api/experts");
  const sel = $("target");
  sel.innerHTML = "";
  for (const e of data.experts) {
    const o = document.createElement("option");
    o.value = e.name;
    o.textContent = e.global ? "global (compartilhado)"
      : `${e.name} (${e.knowledge ?? "?"} conhecimentos)`;
    sel.appendChild(o);
  }
}

$("learn-btn").addEventListener("click", async () => {
  const { expert, global } = currentTarget();
  const fd = new FormData();
  fd.append("expert", expert);
  if (global) fd.append("global_brain", "true");
  if ($("sync").checked) fd.append("sync_flag", "true");
  const m = $("mode").value;
  if (m === "path") {
    fd.append("path", $("path").value);
  } else if (m === "url") {
    fd.append("url", $("url").value);
  } else {
    for (const f of $("files").files) fd.append("files", f);
  }
  $("learn-result").classList.remove("hidden");
  $("learn-result").textContent = "Processando…";
  try {
    const data = await api("/api/learn", { method: "POST", body: fd });
    $("learn-result").textContent = JSON.stringify(data, null, 2);
    await loadJobs();
  } catch (err) {
    $("learn-result").textContent = "Erro: " + err.message;
  }
});

async function loadJobs() {
  const { expert, global } = currentTarget();
  const q = new URLSearchParams({ expert });
  if (global) q.set("global_brain", "true");
  const data = await api("/api/jobs?" + q.toString());
  const tbody = $("jobs-table").querySelector("tbody");
  const jobs = data.jobs || [];
  $("jobs-meta").textContent = jobs.length + " job(s)";
  if (!jobs.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="muted">Nenhum job.</td></tr>';
    return;
  }
  tbody.innerHTML = jobs.map((j) => `
    <tr>
      <td>${j.id}</td><td>${j.command}</td>
      <td class="status-${j.status}">${j.status}</td>
      <td>${(j.created_at || "").replace("T", " ").slice(0, 19)}</td>
      <td>${(j.completed_at || "").replace("T", " ").slice(0, 19)}</td>
      <td>${j.error || ""}</td>
    </tr>`).join("");
}

$("refresh-jobs").addEventListener("click", loadJobs);
$("target").addEventListener("change", loadJobs);

$("check-btn").addEventListener("click", async () => {
  const { expert, global } = currentTarget();
  const q = new URLSearchParams({ expert });
  if (global) q.set("global_brain", "true");
  $("check-result").classList.remove("hidden");
  try {
    const data = await api("/api/check?" + q.toString());
    $("check-result").textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    $("check-result").textContent = "Erro: " + err.message;
  }
});

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function loadStatus() {
  try {
    const data = await api("/api/status");
    $("status-box").innerHTML = ["redis", "celery", "docker"].map((k) => {
      const s = data[k] || {};
      const ok = !!s.ok;
      return `<div style="margin:8px 0;padding:8px 10px;border:1px solid #22262f;border-radius:6px">
        <strong>${k}</strong> <span class="status-${ok ? 'completed' : 'failed'}">${ok ? 'OK' : 'FALHA'}</span>
        <div class="muted">${escapeHtml(s.detail || '')}</div>
      </div>`;
    }).join("");
  } catch (err) {
    $("status-box").innerHTML = `<p class="muted">Erro: ${escapeHtml(err.message)}</p>`;
  }
}

$("refresh-status").addEventListener("click", loadStatus);

async function boot() {
  try {
    const me = await api("/api/me");
    $("who").textContent = "logado como " + me.user;
    showApp();
    await loadExperts();
    await loadJobs();
    await loadStatus();
  } catch (err) { showLogin(); }
}

boot();
setInterval(() => { if (!$("app-view").classList.contains("hidden")) loadJobs().catch(() => {}); }, 5000);
setInterval(() => { if (!$("app-view").classList.contains("hidden")) loadStatus().catch(() => {}); }, 30000);
</script>
</body>
</html>
"""
