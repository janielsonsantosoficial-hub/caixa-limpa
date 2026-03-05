import os
import json
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest

from db import init_db, upsert_user, save_token, load_token, list_active_users, log_cleanup_run, last_runs
from gmail_service import get_gmail_service, executar_limpeza_completa


app = FastAPI()

# Templates + CSS
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
REDIRECT_URI = f"{BASE_URL}/auth/callback"

GOOGLE_CLIENT_SECRET_JSON = os.getenv("GOOGLE_CLIENT_SECRET_JSON", "")
CRON_SECRET = os.getenv("CRON_SECRET", "")

# (MVP) memória local do flow
FLOW_STORE: dict[str, Flow] = {}

def build_flow() -> Flow:
    if not GOOGLE_CLIENT_SECRET_JSON:
        raise RuntimeError("GOOGLE_CLIENT_SECRET_JSON não definido")
    client_config = json.loads(GOOGLE_CLIENT_SECRET_JSON)
    return Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

def creds_from_db(user_id: int) -> Credentials | None:
    token_json = load_token(user_id)
    if not token_json:
        return None
    data = json.loads(token_json)
    creds = Credentials.from_authorized_user_info(data, scopes=SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
            save_token(user_id, creds.to_json())
        else:
            return None
    return creds

@app.on_event("startup")
def startup():
    init_db()

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "base_url": BASE_URL,
    })

@app.get("/auth/login")
def auth_login(email: str):
    flow = build_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    FLOW_STORE[state] = flow
    flow._caixa_email = email  # type: ignore[attr-defined]
    return RedirectResponse(auth_url)

@app.get("/auth/callback", response_class=HTMLResponse)
def auth_callback(request: Request):
    state = request.query_params.get("state")
    if not state or state not in FLOW_STORE:
        return HTMLResponse("State inválido/expirado. Volte e tente novamente.", status_code=400)

    flow = FLOW_STORE.pop(state)
    flow.fetch_token(authorization_response=str(request.url))
    creds = flow.credentials

    email = getattr(flow, "_caixa_email", None)
    if not email:
        return HTMLResponse("Email ausente no fluxo. Refaça /auth/login?email=...", status_code=400)

    user_id = upsert_user(email)
    save_token(user_id, creds.to_json())

    # executa limpeza completa
    service = get_gmail_service(creds)
    resultado = executar_limpeza_completa(service, max_results_por_grupo=200)
    log_cleanup_run(user_id, resultado)

    return templates.TemplateResponse("resultado.html", {
        "request": request,
        "email": email,
        "resultado": resultado,
        "base_url": BASE_URL,
    })

@app.get("/limpar-agora")
def limpar_agora(email: str, max: int = 200):
    user_id = upsert_user(email)
    creds = creds_from_db(user_id)
    if not creds:
        return JSONResponse({"erro": "Sem token válido. Faça login primeiro pela página inicial."}, status_code=401)

    service = get_gmail_service(creds)
    resultado = executar_limpeza_completa(service, max_results_por_grupo=max)
    log_cleanup_run(user_id, resultado)

    return {"ok": True, "email": email, "resultado": resultado}

@app.get("/relatorio")
def relatorio(email: str, limit: int = 10):
    return {"email": email, "ultimas_execucoes": last_runs(email, limit=limit)}

@app.get("/cron/cleanup")
def cron_cleanup(secret: str, max: int = 200):
    if not CRON_SECRET or secret != CRON_SECRET:
        return JSONResponse({"erro": "forbidden"}, status_code=403)

    users = list_active_users()
    total = {"promocoes": 0, "notificacoes": 0, "quarentena": 0, "lixo_moved": 0, "lixo_trashed": 0}
    processed = 0

    for u in users:
        user_id = u["id"]
        email = u["email"]
        creds = creds_from_db(user_id)
        if not creds:
            continue

        service = get_gmail_service(creds)
        resultado = executar_limpeza_completa(service, max_results_por_grupo=max)
        log_cleanup_run(user_id, resultado)

        total["promocoes"] += int(resultado["promocoes"]["moved"])
        total["notificacoes"] += int(resultado["notificacoes"]["moved"])
        total["quarentena"] += int(resultado["quarentena"]["moved"])
        total["lixo_moved"] += int(resultado["lixo"]["moved"])
        total["lixo_trashed"] += int(resultado["lixo"]["trashed"])
        processed += 1

    return {"ok": True, "usuarios_processados": processed, "total": total}
