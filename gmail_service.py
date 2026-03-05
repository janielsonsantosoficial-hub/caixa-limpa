from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


USER_ID = "me"

LABELS = {
    "PROMOCOES": "CAIXA_LIMPA/PROMOCOES",
    "NOTIFICACOES": "CAIXA_LIMPA/NOTIFICACOES",
    "QUARENTENA": "CAIXA_LIMPA/QUARENTENA",
    "LIXO": "CAIXA_LIMPA/LIXO",
}

def get_gmail_service(creds: Credentials):
    return build("gmail", "v1", credentials=creds)

def _list_labels(service):
    resp = service.users().labels().list(userId=USER_ID).execute()
    return resp.get("labels", [])

def _get_label_id_by_name(service, name: str) -> str | None:
    for lb in _list_labels(service):
        if lb.get("name") == name:
            return lb.get("id")
    return None

def _create_label(service, name: str) -> str:
    body = {
        "name": name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
        "type": "user",
    }
    resp = service.users().labels().create(userId=USER_ID, body=body).execute()
    return resp["id"]

def ensure_caixa_limpa_labels(service):
    ids = {}
    for key, name in LABELS.items():
        label_id = _get_label_id_by_name(service, name)
        if not label_id:
            label_id = _create_label(service, name)
        ids[key] = label_id
    return ids

def _search_message_ids(service, query: str, max_results: int):
    ids = []
    page_token = None
    while len(ids) < max_results:
        resp = service.users().messages().list(
            userId=USER_ID,
            q=query,
            maxResults=min(500, max_results - len(ids)),
            pageToken=page_token
        ).execute()
        msgs = resp.get("messages", [])
        ids.extend([m["id"] for m in msgs])
        page_token = resp.get("nextPageToken")
        if not page_token or not msgs:
            break
    return ids

def _batch_modify_move(service, message_ids, add_label_id: str | None, remove_inbox: bool = True):
    if not message_ids:
        return 0
    body = {
        "ids": message_ids,
        "addLabelIds": [add_label_id] if add_label_id else [],
        "removeLabelIds": ["INBOX"] if remove_inbox else [],
    }
    service.users().messages().batchModify(userId=USER_ID, body=body).execute()
    return len(message_ids)

def _trash_messages(service, message_ids):
    # Trash é 1 por 1 (não tem batchTrash oficial)
    count = 0
    for mid in message_ids:
        service.users().messages().trash(userId=USER_ID, id=mid).execute()
        count += 1
    return count

def executar_limpeza_completa(service, max_results_por_grupo: int = 200):
    """
    Regra:
    - Importantes: não toca (fica na INBOX)
    - Promoções: category:promotions older_than:2d -> PROMOCOES (remove INBOX)
    - Notificações: category:social OR category:updates older_than:1d -> NOTIFICACOES (remove INBOX)
    - Lixo/Spam: in:spam OR (category:promotions older_than:15d) -> LIXO (remove INBOX) e opcional trash
    - Quarentena: “resto” opcional (aqui vamos usar updates older_than:5d que não caiu em notificações)
    """
    ids = ensure_caixa_limpa_labels(service)

    resultado = {
        "promocoes": {"query": "", "moved": 0},
        "notificacoes": {"query": "", "moved": 0},
        "quarentena": {"query": "", "moved": 0},
        "lixo": {"query": "", "moved": 0, "trashed": 0},
    }

    # 1) Promoções (2 dias)
    q_promocoes = "in:inbox category:promotions older_than:2d"
    prom_ids = _search_message_ids(service, q_promocoes, max_results_por_grupo)
    moved = _batch_modify_move(service, prom_ids, ids["PROMOCOES"], remove_inbox=True)
    resultado["promocoes"] = {"query": q_promocoes, "moved": moved}

    # 2) Notificações (1 dia) - social + updates
    q_notif = "(in:inbox category:social older_than:1d) OR (in:inbox category:updates older_than:1d)"
    notif_ids = _search_message_ids(service, q_notif, max_results_por_grupo)
    moved = _batch_modify_move(service, notif_ids, ids["NOTIFICACOES"], remove_inbox=True)
    resultado["notificacoes"] = {"query": q_notif, "moved": moved}

    # 3) Quarentena (5 dias) - updates mais antigos (o que sobrou)
    q_quar = "in:inbox category:updates older_than:5d"
    quar_ids = _search_message_ids(service, q_quar, max_results_por_grupo)
    moved = _batch_modify_move(service, quar_ids, ids["QUARENTENA"], remove_inbox=True)
    resultado["quarentena"] = {"query": q_quar, "moved": moved}

    # 4) Lixo/Spam
    # - Spam: joga no lixo direto
    q_spam = "in:spam"
    spam_ids = _search_message_ids(service, q_spam, max_results_por_grupo)
    trashed = _trash_messages(service, spam_ids)

    # - Promoções muito antigas (15 dias) vira lixo
    q_lixo = "in:inbox category:promotions older_than:15d"
    lixo_ids = _search_message_ids(service, q_lixo, max_results_por_grupo)
    moved = _batch_modify_move(service, lixo_ids, ids["LIXO"], remove_inbox=True)

    resultado["lixo"] = {"query": f"{q_spam} + {q_lixo}", "moved": moved, "trashed": trashed}

    return resultado
