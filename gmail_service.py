import base64
import os
import re
from typing import Dict, List, Tuple

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials


# =========================
# CONFIG (pode ajustar)
# =========================

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

# Regras de “idade”
PROMO_OLDER_THAN = os.getenv("PROMO_OLDER_THAN", "2d")         # promoções após 2 dias
NOTIF_OLDER_THAN = os.getenv("NOTIF_OLDER_THAN", "1d")         # notificações após 1 dia
UPDATES_OLDER_THAN = os.getenv("UPDATES_OLDER_THAN", "5d")     # atualizações após 5 dias

# Lixo/Spam
DELETE_SPAM_DIRECT = os.getenv("DELETE_SPAM_DIRECT", "true").lower() == "true"
LIXO_PROMO_OLDER_THAN = os.getenv("LIXO_PROMO_OLDER_THAN", "30d")  # promoções muito antigas -> lixo (opcional)

# “IMPORTANTES”: lista de termos/domínios para NÃO mexer (allowlist).
# Tudo que bater aqui será excluído das movimentações.
# Separe por vírgula
IMPORTANT_ALLOWLIST = [
    x.strip().lower()
    for x in os.getenv(
        "IMPORTANT_ALLOWLIST",
        "banco,bradesco,itau,santander,nubank,inter,caixa,bb,work,trabalho,cliente,clientes,familia,família,comprovante,nota fiscal,nf,recibo,pagamento,fatura,contrato"
    ).split(",")
    if x.strip()
]

# Labels
LABEL_ROOT = "CAIXA_LIMPA"
LABEL_IMPORTANTES = f"{LABEL_ROOT}/IMPORTANTES"
LABEL_PROMOCOES = f"{LABEL_ROOT}/PROMOCOES"
LABEL_NOTIFICACOES = f"{LABEL_ROOT}/NOTIFICACOES"
LABEL_QUARENTENA = f"{LABEL_ROOT}/QUARENTENA"   # vamos usar para UPDATES (ex: atualizações)
LABEL_LIXO = f"{LABEL_ROOT}/LIXO"


# =========================
# Helpers
# =========================

def get_gmail_service(creds: Credentials):
    return build("gmail", "v1", credentials=creds)


def _normalize(s: str) -> str:
    s = s.lower().strip()
    # remove acentos simples (opcional). Mantive simples para não depender de lib externa.
    s = s.replace("á", "a").replace("à", "a").replace("ã", "a").replace("â", "a")
    s = s.replace("é", "e").replace("ê", "e")
    s = s.replace("í", "i")
    s = s.replace("ó", "o").replace("ô", "o").replace("õ", "o")
    s = s.replace("ú", "u")
    s = s.replace("ç", "c")
    return s


def _needs_skip_by_allowlist(query: str) -> str:
    """
    Constrói um trecho de query do Gmail para excluir termos importantes.
    Ex: -("banco" OR "cliente" OR "fatura")
    """
    if not IMPORTANT_ALLOWLIST:
        return query

    terms = []
    for t in IMPORTANT_ALLOWLIST:
        t2 = t.replace('"', "")
        if not t2:
            continue
        # coloca como frase
        terms.append(f'"{t2}"')

    if not terms:
        return query

    block = " OR ".join(terms)
    return f'{query} -({block})'


def _get_label_map(service) -> Dict[str, str]:
    res = service.users().labels().list(userId="me").execute()
    labels = res.get("labels", [])
    return {l["name"]: l["id"] for l in labels}


def _create_label(service, name: str) -> str:
    body = {
        "name": name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    }
    created = service.users().labels().create(userId="me", body=body).execute()
    return created["id"]


def ensure_caixa_limpa_labels(service) -> Dict[str, str]:
    """
    Garante a criação de:
      CAIXA_LIMPA/IMPORTANTES
      CAIXA_LIMPA/PROMOCOES
      CAIXA_LIMPA/NOTIFICACOES
      CAIXA_LIMPA/QUARENTENA
      CAIXA_LIMPA/LIXO

    Retorna dict: {nome_label: id_label}
    """
    label_map = _get_label_map(service)
    needed = [LABEL_IMPORTANTES, LABEL_PROMOCOES, LABEL_NOTIFICACOES, LABEL_QUARENTENA, LABEL_LIXO]

    for name in needed:
        if name not in label_map:
            label_id = _create_label(service, name)
            label_map[name] = label_id

    return label_map


def _list_message_ids(service, q: str, max_results: int) -> List[str]:
    ids: List[str] = []
    page_token = None

    while len(ids) < max_results:
        resp = service.users().messages().list(
            userId="me",
            q=q,
            maxResults=min(500, max_results - len(ids)),
            pageToken=page_token,
        ).execute()

        msgs = resp.get("messages", [])
        ids.extend([m["id"] for m in msgs])

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return ids


def _batch_move_to_label(service, message_ids: List[str], add_label_id: str, remove_inbox: bool = True) -> int:
    if not message_ids:
        return 0

    body = {
        "ids": message_ids,
        "addLabelIds": [add_label_id],
        "removeLabelIds": ["INBOX"] if remove_inbox else [],
    }
    service.users().messages().batchModify(userId="me", body=body).execute()
    return len(message_ids)


def _trash_messages(service, message_ids: List[str]) -> int:
    """
    Gmail API não tem batchTrash.
    Então fazemos em loop (seguro e simples).
    """
    count = 0
    for mid in message_ids:
        service.users().messages().trash(userId="me", id=mid).execute()
        count += 1
    return count


# =========================
# Regras (4 grupos)
# =========================

def mover_promocoes(service, label_id_promocoes: str, max_results: int = 200) -> int:
    q = f"category:promotions older_than:{PROMO_OLDER_THAN} in:inbox"
    q = _needs_skip_by_allowlist(q)
    ids = _list_message_ids(service, q, max_results)
    return _batch_move_to_label(service, ids, label_id_promocoes, remove_inbox=True)


def mover_notificacoes(service, label_id_notificacoes: str, max_results: int = 200) -> int:
    # Social + Updates leves geralmente são “notificações”
    # Aqui usamos category:social como NOTIFICACOES
    q = f"category:social older_than:{NOTIF_OLDER_THAN} in:inbox"
    q = _needs_skip_by_allowlist(q)
    ids = _list_message_ids(service, q, max_results)
    return _batch_move_to_label(service, ids, label_id_notificacoes, remove_inbox=True)


def mover_atualizacoes_para_quarentena(service, label_id_quarentena: str, max_results: int = 200) -> int:
    # Updates = recibos automáticos, sistemas, etc (depois de 5 dias vai para QUARENTENA)
    q = f"category:updates older_than:{UPDATES_OLDER_THAN} in:inbox"
    q = _needs_skip_by_allowlist(q)
    ids = _list_message_ids(service, q, max_results)
    return _batch_move_to_label(service, ids, label_id_quarentena, remove_inbox=True)


def lixo_promocoes_muito_antigas(service, label_id_lixo: str, max_results: int = 200) -> int:
    """
    Opcional: promoções muito antigas podem virar LIXO.
    """
    if not LIXO_PROMO_OLDER_THAN:
        return 0
    q = f"category:promotions older_than:{LIXO_PROMO_OLDER_THAN} in:anywhere"
    q = _needs_skip_by_allowlist(q)
    ids = _list_message_ids(service, q, max_results)
    # Aqui eu não removo INBOX porque elas já não deveriam estar no inbox; só etiqueta e organiza.
    return _batch_move_to_label(service, ids, label_id_lixo, remove_inbox=False)


def limpar_spam(service, max_results: int = 200) -> int:
    """
    Spam = pode mandar para lixeira direto.
    """
    if not DELETE_SPAM_DIRECT:
        return 0
    q = "is:spam"
    ids = _list_message_ids(service, q, max_results)
    return _trash_messages(service, ids)


def executar_limpeza_completa(service, max_results_por_grupo: int = 200) -> Dict[str, int]:
    """
    Roda todas as regras:
    1) Promoções -> PROMOCOES (2d)
    2) Notificações -> NOTIFICACOES (1d)
    3) Atualizações -> QUARENTENA (5d)
    4) Spam -> lixeira
    + opcional: promoções muito antigas -> LIXO
    """
    label_map = ensure_caixa_limpa_labels(service)

    moved_promos = mover_promocoes(service, label_map[LABEL_PROMOCOES], max_results=max_results_por_grupo)
    moved_notifs = mover_notificacoes(service, label_map[LABEL_NOTIFICACOES], max_results=max_results_por_grupo)
    moved_updates = mover_atualizacoes_para_quarentena(service, label_map[LABEL_QUARENTENA], max_results=max_results_por_grupo)
    spam_trashed = limpar_spam(service, max_results=max_results_por_grupo)
    moved_lixo = lixo_promocoes_muito_antigas(service, label_map[LABEL_LIXO], max_results=max_results_por_grupo)

    return {
        "promocoes_movidas": moved_promos,
        "notificacoes_movidas": moved_notifs,
        "atualizacoes_para_quarentena": moved_updates,
        "spam_enviado_lixeira": spam_trashed,
        "promocoes_muito_antigas_para_lixo": moved_lixo,
    }
