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
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .brain_tool import check, count_pages, learn, list_jobs, register_upload_asset, __version__
from .db import get_brain_root, get_session, list_expert_names, validate_expert_identifier
from .brain import (
    resolve_hermes_profile_dir,
    _hermes_config_get,
    _hermes_config_set,
    _read_fallback_chain,
    _read_profile_env,
    _set_fallback_providers,
    _update_profile_env,
    _write_soul,
    hermes_profiles_root,
)

__all__ = [
    "add_dashboard_user", "list_dashboard_users", "remove_dashboard_user",
    "authenticate", "get_access_token", "create_app", "serve",
    "stop_dashboard", "dashboard_status", "main",
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


def rotate_access_token() -> str:
    """Gera e persiste um novo token, invalidando o token anterior."""
    if os.environ.get("BRAIN_DASHBOARD_TOKEN"):
        raise RuntimeError(
            "BRAIN_DASHBOARD_TOKEN está definido; remova-o para gerar um token novo"
        )
    path = get_brain_root() / ".dashboard_token"
    generated = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(generated, encoding="utf-8")
    os.chmod(path, 0o600)
    return generated


def discard_access_token() -> None:
    """Descarta o token persistido ao encerrar a sessão do dashboard."""
    (get_brain_root() / ".dashboard_token").unlink(missing_ok=True)


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
#
# A leitura de REDIS_URL / CELERY_BROKER_URL é feita via ambiente.
# Se não estiverem definidos, assume que não há broker configurado (em vez de
# assumir localhost:6379, que falharia se o Redis estiver só no Docker).

def _redis_status() -> dict:
    url = os.environ.get("REDIS_URL") or os.environ.get("CELERY_BROKER_URL")
    if not url:
        return {"ok": False, "url": None, "detail": "REDIS_URL/CELERY_BROKER_URL não definidos"}
    try:
        import redis

        r = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        r.ping()
        return {"ok": True, "url": url, "detail": "pong"}
    except Exception as e:  # noqa: BLE001 — status é best-effort
        return {"ok": False, "url": url, "detail": str(e)}


def _celery_status() -> dict:
    broker = os.environ.get("CELERY_BROKER_URL") or os.environ.get("REDIS_URL")
    if not broker:
        return {"ok": False, "broker": None, "detail": "CELERY_BROKER_URL/REDIS_URL não definidos"}
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
    return list_expert_names()


def _uploads_dir() -> Path:
    d = get_brain_root() / ".uploads"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def _require_profile(name: str) -> str:
    """Resolve o diretório do profile Hermes para um expert (404 se não existe).

    Delega a `resolve_hermes_profile_dir`, que já bloqueia path traversal
    (rejeita barra, barra invertida, "." e "..") e só retorna diretórios
    reais sob a pasta de profiles do Hermes.
    """
    pdir = resolve_hermes_profile_dir(name)
    if pdir is None:
        raise HTTPException(
            status_code=404, detail=f"profile Hermes '{name}' nao encontrado"
        )
    return pdir


# --- App --------------------------------------------------------------------

def create_app(secret: Optional[str] = None, token: Optional[str] = None) -> FastAPI:
    secret = secret or _load_or_create_secret()
    configured_token = token
    secure_cookies = os.environ.get("BRAIN_DASHBOARD_SECURE_COOKIES", "") in ("1", "true", "yes")
    limiter = _LoginRateLimiter(_LOGIN_MAX_FAILURES, _LOGIN_LOCKOUT_SECONDS)

    # Templates
    templates_dir = Path(__file__).parent.parent / "templates"
    templates = Jinja2Templates(directory=str(templates_dir))

    def current_access_token() -> str:
        # Apps created without an explicit token follow the persisted session,
        # so `brain dashboard token --rotate` takes effect without a restart.
        return configured_token if configured_token is not None else _load_or_create_token()

    app = FastAPI(title="Brain Dashboard", docs_url=None, redoc_url=None)

    def current_user(request: Request) -> str:
        # 1) sessão por cookie (login por senha OU troca de token)
        cookie = request.cookies.get(_COOKIE)
        user = _verify_token(secret, cookie) if cookie else None
        if user:
            return user
        # 2) token bearer (Authorization: Bearer <token>)
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer ") and hmac.compare_digest(
            auth[7:].strip(), current_access_token()
        ):
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
    def index(request: Request, token: Optional[str] = None):
        if token:
            if hmac.compare_digest(token, current_access_token()):
                _audit("token_login")
                resp = RedirectResponse("/", status_code=302)
                resp.set_cookie(_COOKIE, _make_token(secret, "admin"),
                                httponly=True, samesite="strict", secure=secure_cookies,
                                max_age=_SESSION_TTL)
                return resp
            return RedirectResponse("/", status_code=302)
        # render index using template
        return templates.TemplateResponse(request, "dashboard/index.html", {
            "version": __version__,
        })

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
        if not hmac.compare_digest(token, current_access_token()):
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
                content = await f.read(MAX_UPLOAD_BYTES + 1)
                if len(content) > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"arquivo excede o limite de {MAX_UPLOAD_BYTES // (1024 * 1024)}MB",
                    )
                asset_ref = register_upload_asset(safe_name, content)
                results.append(learn(conn, target, asset_ref, sync_immediately=sync_flag))
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

    # --- Profiles: SOUL.md (persona) + brain (LLM) --------------------------

    @app.get("/api/profiles")
    def api_profiles(user: str = Depends(current_user)):
        profiles = []
        for name in _expert_names():
            pdir = resolve_hermes_profile_dir(name)
            profiles.append({
                "name": name,
                "hermes_profile": os.path.basename(pdir) if pdir else None,
                "has_soul": bool(pdir and (Path(pdir) / "SOUL.md").exists()),
            })
        return {"profiles": profiles}

    @app.get("/api/profiles/{name}/soul")
    def api_get_soul(name: str, user: str = Depends(current_user)):
        pdir = _require_profile(name)
        soul_path = Path(pdir) / "SOUL.md"
        content = soul_path.read_text(encoding="utf-8") if soul_path.exists() else ""
        return {"name": name, "soul": content}

    @app.post("/api/profiles/{name}/soul")
    async def api_set_soul(
        name: str,
        content: str = Form(...),
        _origin: None = Depends(same_origin),
        user: str = Depends(current_user),
    ):
        pdir = _require_profile(name)
        _write_soul(str(Path(pdir) / "SOUL.md"), content)
        _audit("soul_set", user=user, profile=name)
        return {"ok": True, "name": name}

    @app.get("/api/profiles/{name}/model")
    def api_get_model(name: str, user: str = Depends(current_user)):
        pdir = _require_profile(name)
        return {
            "name": name,
            "model": _hermes_config_get(pdir, "model.default"),
            "provider": _hermes_config_get(pdir, "model.provider"),
            "base_url": _hermes_config_get(pdir, "model.base_url"),
            "fallback": _read_fallback_chain(pdir),
        }

    @app.post("/api/profiles/{name}/model")
    async def api_set_model(
        name: str,
        model: str = Form(""),
        provider: str = Form(""),
        base_url: str = Form(""),
        fallback_model: str = Form(""),
        fallback_provider: str = Form(""),
        _origin: None = Depends(same_origin),
        user: str = Depends(current_user),
    ):
        pdir = _require_profile(name)
        model = (model or "").strip()
        provider = (provider or "").strip()
        base_url = (base_url or "").strip()
        fallback_model = (fallback_model or "").strip()
        fallback_provider = (fallback_provider or "").strip()

        if bool(fallback_model) != bool(fallback_provider):
            raise HTTPException(
                status_code=400, detail="fallback model e provider devem vir juntos"
            )

        errors: List[str] = []
        if model:
            errors += _hermes_config_set(pdir, "model.default", model)
        if provider:
            errors += _hermes_config_set(pdir, "model.provider", provider)
        if base_url:
            errors += _hermes_config_set(pdir, "model.base_url", base_url)
        if fallback_model:
            errors += _set_fallback_providers(
                pdir, [{"provider": fallback_provider, "model": fallback_model}]
            )

        if not errors and provider == "ollama":
            errors += _update_profile_env(str(pdir), {
                "BRAIN_OLLAMA_ENABLED": "true",
                "BRAIN_OLLAMA_BASE_URL": base_url or "http://localhost:11434",
                "BRAIN_OLLAMA_DEFAULT_MODEL": model or "hermes3:3b",
            })

        if errors:
            raise HTTPException(status_code=500, detail="; ".join(errors))

        _audit("model_set", user=user, profile=name)
        return {"ok": True, "name": name}

    @app.get("/api/ollama/models")
    async def api_ollama_models(base_url: str = "http://localhost:11434", user: str = Depends(current_user)):
        """Lista modelos disponíveis no Ollama local."""
        from brain_tool.brain import _ollama_list_models
        models = _ollama_list_models(base_url)
        return {"models": models, "base_url": base_url}

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request, user: str = Depends(current_user)):
        """Página de configurações (Ollama, etc.)."""
        return templates.TemplateResponse(request, "dashboard/settings.html", {
            "version": __version__,
        })

    @app.get("/api/settings/ollama")
    async def api_get_ollama_settings(
        profile: Optional[str] = None,
        user: str = Depends(current_user),
    ):
        """Retorna configuração do Ollama do profile Hermes selecionado."""
        from brain_tool.brain import (
            _ollama_check_installed, _ollama_check_running, _ollama_list_models,
        )
        profiles_root = Path(hermes_profiles_root())
        profiles = sorted(
            p.name for p in profiles_root.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        ) if profiles_root.is_dir() else []
        if not profile:
            return {"profiles": profiles, "profile": None}
        pdir = _require_profile(profile)
        env = _read_profile_env(str(pdir))
        provider = _hermes_config_get(pdir, "model.provider") or ""
        base_url = env.get("BRAIN_OLLAMA_BASE_URL") or _hermes_config_get(
            pdir, "model.base_url"
        ) or "http://localhost:11434"
        default_model = env.get("BRAIN_OLLAMA_DEFAULT_MODEL") or _hermes_config_get(
            pdir, "model.default"
        ) or "hermes3:3b"
        enabled = env.get("BRAIN_OLLAMA_ENABLED", "").lower() == "true" or provider == "ollama"
        installed = _ollama_check_installed()
        running = _ollama_check_running(base_url) if installed else False
        models = _ollama_list_models(base_url) if running else []
        return {
            "profiles": profiles,
            "profile": profile,
            "enabled": enabled,
            "base_url": base_url,
            "default_model": default_model,
            "installed": installed,
            "running": running,
            "models": models,
        }

    @app.post("/api/settings/ollama")
    async def api_set_ollama_settings(
        profile: str = Form(...),
        enabled: bool = Form(False),
        base_url: str = Form("http://localhost:11434"),
        default_model: str = Form("hermes3:3b"),
        install_if_missing: bool = Form(False),
        pull_model: str = Form(""),
        user: str = Depends(current_user),
    ):
        """Configura Ollama no .env e config.yaml do profile selecionado."""
        import shutil
        import subprocess
        from brain_tool.brain import _ollama_check_running
        pdir = _require_profile(profile)
        base_url = (base_url or "http://localhost:11434").strip()
        default_model = (default_model or "hermes3:3b").strip()
        errors: List[str] = []

        if enabled:
            errors += _update_profile_env(str(pdir), {
                "BRAIN_OLLAMA_ENABLED": "true",
                "BRAIN_OLLAMA_BASE_URL": base_url,
                "BRAIN_OLLAMA_DEFAULT_MODEL": default_model,
            })
            if not errors:
                errors += _hermes_config_set(pdir, "model.provider", "ollama")
                errors += _hermes_config_set(pdir, "model.default", default_model)
                errors += _hermes_config_set(pdir, "model.base_url", base_url)
            if install_if_missing and not shutil.which("ollama"):
                result = subprocess.run(
                    "curl -fsSL https://ollama.com/install.sh | sh",
                    shell=True, capture_output=True, text=True, timeout=120,
                )
                if result.returncode != 0:
                    return {"ok": False, "error": f"Falha ao instalar Ollama: {result.stderr}"}
        else:
            errors += _update_profile_env(str(pdir), {
                "BRAIN_OLLAMA_ENABLED": "false",
                "BRAIN_OLLAMA_BASE_URL": base_url,
                "BRAIN_OLLAMA_DEFAULT_MODEL": default_model,
            })

        if errors:
            raise HTTPException(status_code=500, detail="; ".join(errors))

        if pull_model:
            if not shutil.which("ollama"):
                return {"ok": False, "error": "Ollama não instalado; habilite a instalação primeiro"}
            if not _ollama_check_running(base_url):
                return {"ok": False, "error": "Ollama instalado, mas o servidor não está rodando"}
            result = subprocess.run(
                ["ollama", "pull", pull_model.strip()],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                return {"ok": False, "error": f"Falha ao baixar modelo: {result.stderr}"}

        _audit("ollama_settings_set", user=user, profile=profile)
        return {"ok": True, "profile": profile,
                "message": "Configuração do profile salva. Reinicie o expert para aplicar."}

    return app


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, foreground: bool = False) -> int:
    """Sobe o dashboard. Por padrão roda em background (não bloqueia o terminal);
    use foreground=True (ou `--foreground`) para bloquear (debug/dev)."""
    rotate_access_token()
    if foreground:
        return _run_server(host, port)
    return _start_detached(host, port)


def _print_banner(token: str, host: str, port: int) -> None:
    print("\n=== Brain Dashboard ===")
    print(f"  Token de acesso: {token}")
    print(f"  Local:  http://127.0.0.1:{port}/?token={token}")
    if host not in ("127.0.0.1", "localhost", "::1"):
        lan = _lan_ip()
        if lan:
            print(f"  LAN:    http://{lan}:{port}/?token={token}")
    print("  (login por usuário/senha continua disponível, se configurado)")


def _run_server(host: str, port: int) -> int:
    import uvicorn

    access_token = _load_or_create_token()
    _print_banner(access_token, host, port)
    print()
    uvicorn.run(create_app(), host=host, port=port, log_level="info")
    return 0


def _pid_file() -> Path:
    return get_brain_root() / ".dashboard.pid"


def _log_file() -> Path:
    return get_brain_root() / ".dashboard.log"


def _start_detached(host: str, port: int) -> int:
    """Inicia o servidor em background (sessão nova), grava PID e devolve o terminal."""
    token = _load_or_create_token()
    log_path = _log_file()
    log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    logf = open(log_path, "a")
    cmd = [sys.executable, "-m", "brain_tool.dashboard",
           "--host", host, "--port", str(port), "--foreground"]
    kwargs = {"stdout": logf, "stderr": subprocess.STDOUT,
              "stdin": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    _pid_file().write_text(str(proc.pid))
    _print_banner(token, host, port)
    print(f"  Rodando em background (PID {proc.pid}).")
    print(f"  Log:    {log_path}")
    print("  Parar:  brain dashboard stop")
    print()
    return 0


def stop_dashboard() -> bool:
    """Encerra o dashboard em background (via PID file). Retorna True se parou algo."""
    pid_path = _pid_file()
    if not pid_path.exists():
        discard_access_token()
        return False
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        pid_path.unlink(missing_ok=True)
        discard_access_token()
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pid_path.unlink(missing_ok=True)
        discard_access_token()
        return False
    except PermissionError:
        discard_access_token()
        return False
    pid_path.unlink(missing_ok=True)
    discard_access_token()
    return True


def dashboard_status() -> dict:
    """Estado do dashboard em background (running/pid), sem efeito colateral."""
    pid_path = _pid_file()
    if not pid_path.exists():
        return {"running": False}
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return {"running": False}
    try:
        os.kill(pid, 0)
        return {"running": True, "pid": pid}
    except (ProcessLookupError, PermissionError):
        return {"running": False}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="brain-dashboard")
    parser.add_argument("--host", default=os.environ.get("BRAIN_DASHBOARD_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("BRAIN_DASHBOARD_PORT", str(DEFAULT_PORT))))
    parser.add_argument("--foreground", action="store_true",
                        help="roda em primeiro plano (bloqueia o terminal)")
    args = parser.parse_args()
    return serve(host=args.host, port=args.port, foreground=args.foreground)


if __name__ == "__main__":
    sys.exit(main())
