import os
import json
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, JSONResponse
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest

from db import (
    init_db, upsert_user, save_token, load_token,
    list_active_users, log_cleanup_run, last_runs
)
from gmail_service import get_gmail_service, executar_limpeza_completa

app = FastAPI()

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")
REDIRECT_URI = f"{BASE_URL}/auth/callback"

GOOGLE_CLIENT_SECRET_JSON = os.getenv("GOOGLE_CLIENT_SECRET_JSON", "")

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

@app.get("/")
def home():
    return {
        "app": "CAIXA LIMPA API",
        "login": f"{BASE_URL}/auth/login?email=SEU_EMAIL",
        "limpar_agora": f"{BASE_URL}/limpar-agora?email=SEU_EMAIL&max=50",
        "cron_diario": f"{BASE_URL}/cron/cleanup?secret=SEU_SEGREDO",
    }

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

@app.get("/auth/callback")
def auth_callback(request: Request):
    state = request.query_params.get("state")
    if not state or state not in FLOW_STORE:
        return JSONResponse({"erro": "state inválido/expirado. Faça login novamente."}, status_code=400)

    flow = FLOW_STORE.pop(state)
    flow.fetch_token(authorization_response=str(request.url))
    creds = flow.credentials

    email = getattr(flow, "_caixa_email", None)
    if not email:
        return JSONResponse({"erro": "email ausente no fluxo. Refaça /auth/login?email=..."} , status_code=400)

    user_id = upsert_user(email)
    save_token(user_id, creds.to_json())

    service = get_gmail_service(creds)
    _, quar_id, _ = ensure_caixa_limpa_labels(service)
    moved = mover_para_quarentena(service, quar_id, max_results=50)
    log_cleanup_run(user_id, moved)

    return JSONResponse({
        "login": "sucesso",
        "email": email,
        "token_salvo_no_banco": True,
        "emails_movidos_para_quarentena": moved,
        "next": f"{BASE_URL}/limpar-agora?email={email}&max=50"
    })

@app.get("/limpar-agora")
def limpar_agora(email: str, max: int = 50):
    user_id = upsert_user(email)
    creds = creds_from_db(user_id)
    if not creds:
        return JSONResponse({"erro": "Sem token válido. Faça login em /auth/login?email=..."} , status_code=401)

    service = get_gmail_service(creds)
    _, quar_id, _ = ensure_caixa_limpa_labels(service)
    moved = mover_para_quarentena(service, quar_id, max_results=max)
    log_cleanup_run(user_id, moved)

    return {
        "ok": True,
        "email": email,
        "moved": moved,
        "label": "CAIXA_LIMPA/QUARENTENA",
        "max_usado": max,
    }

@app.get("/relatorio")
def relatorio(email: str, limit: int = 10):
    return {"email": email, "ultimas_execucoes": last_runs(email, limit=limit)}

@app.get("/cron/cleanup")
def cron_cleanup(secret: str, max: int = 50):
    CRON_SECRET = os.getenv("CRON_SECRET", "")
    if not CRON_SECRET or secret != CRON_SECRET:
        return JSONResponse({"erro": "forbidden"}, status_code=403)

    users = list_active_users()
    total_moved = 0
    processed = 0

    for u in users:
        user_id = u["id"]
        email = u["email"]
        creds = creds_from_db(user_id)
        if not creds:
            continue

        service = get_gmail_service(creds)
        _, quar_id, _ = ensure_caixa_limpa_labels(service)
        moved = mover_para_quarentena(service, quar_id, max_results=max)
        log_cleanup_run(user_id, moved)

        total_moved += moved
        processed += 1

    return {"ok": True, "usuarios_processados": processed, "total_movidos": total_moved}

