from googleapiclient.discovery import build

LABEL_ROOT = "CAIXA_LIMPA"
LABEL_QUAR = "CAIXA_LIMPA/QUARENTENA"
LABEL_SAFE = "CAIXA_LIMPA/IMPORTANTES"

def get_gmail_service(credentials):
    return build("gmail", "v1", credentials=credentials)

def ensure_label(service, label_name: str) -> str:
    labels_resp = service.users().labels().list(userId="me").execute()
    labels = labels_resp.get("labels", [])
    for lb in labels:
        if lb.get("name") == label_name:
            return lb["id"]

    body = {
        "name": label_name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    }
    created = service.users().labels().create(userId="me", body=body).execute()
    return created["id"]

def ensure_caixa_limpa_labels(service):
    root_id = ensure_label(service, LABEL_ROOT)
    quar_id = ensure_label(service, LABEL_QUAR)
    safe_id = ensure_label(service, LABEL_SAFE)
    return root_id, quar_id, safe_id

def mover_para_quarentena(service, label_quar_id: str, max_results: int = 50) -> int:
    query = (
        'unsubscribe OR "cancelar inscrição" OR newsletter OR promoção OR promocoes '
        'OR oferta OR cupom OR desconto OR marketing OR noreply OR "no-reply" '
        'OR "do not reply" OR "view in browser"'
    )
    resp = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    msgs = resp.get("messages", [])
    count = 0
    for m in msgs:
        service.users().messages().modify(
            userId="me",
            id=m["id"],
            body={"addLabelIds": [label_quar_id], "removeLabelIds": ["INBOX"]},
        ).execute()
        count += 1
    return count