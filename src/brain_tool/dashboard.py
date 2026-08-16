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
import time
import uuid
from pathlib import Path
from typing import List, Optional

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
    return bool(stored) and _verify_password(password, stored)


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

def create_app(secret: Optional[str] = None) -> FastAPI:
    secret = secret or os.environ.get("BRAIN_DASHBOARD_SECRET") or secrets.token_hex(32)

    app = FastAPI(title="Brain Dashboard", docs_url=None, redoc_url=None)

    def current_user(request: Request) -> str:
        token = request.cookies.get(_COOKIE)
        user = _verify_token(secret, token) if token else None
        if not user:
            raise HTTPException(status_code=401, detail="nao autenticado")
        return user

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(_INDEX_HTML)

    @app.post("/login")
    async def login(username: str = Form(...), password: str = Form(...)):
        if not authenticate(username, password):
            return JSONResponse({"ok": False, "error": "credenciais invalidas"},
                                status_code=401)
        resp = JSONResponse({"ok": True, "user": username})
        resp.set_cookie(_COOKIE, _make_token(secret, username),
                        httponly=True, samesite="lax", max_age=_SESSION_TTL)
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
        user: str = Depends(current_user),
        expert: str = Form(...),
        global_brain: bool = Form(False),
        sync_flag: bool = Form(False),
        path: str = Form(""),
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
        uploads = [f for f in (files or []) if f.filename]
        if not path and not uploads:
            raise HTTPException(
                status_code=400, detail="informe um caminho no servidor ou envie arquivos"
            )

        conn = get_session(expert=target, global_brain=global_brain)
        try:
            results = []
            if path:
                results.append(learn(conn, target, path, sync_immediately=sync_flag))
            for f in uploads:
                safe_name = Path(f.filename or "upload").name
                dest = _uploads_dir() / f"{uuid.uuid4().hex}_{safe_name}"
                content = await f.read()
                dest.write_bytes(content)
                os.chmod(dest, 0o600)
                results.append(learn(conn, target, str(dest), sync_immediately=sync_flag))
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

    return app


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> int:
    import uvicorn

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

$("logout").addEventListener("click", () => { location.href = "/logout"; });

$("mode").addEventListener("change", () => {
  const upload = $("mode").value === "upload";
  $("upload-box").classList.toggle("hidden", !upload);
  $("path-box").classList.toggle("hidden", upload);
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
  if ($("mode").value === "path") {
    fd.append("path", $("path").value);
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

async function boot() {
  try {
    const me = await api("/api/me");
    $("who").textContent = "logado como " + me.user;
    showApp();
    await loadExperts();
    await loadJobs();
  } catch (err) { showLogin(); }
}

boot();
setInterval(() => { if (!$("app-view").classList.contains("hidden")) loadJobs().catch(() => {}); }, 5000);
</script>
</body>
</html>
"""
