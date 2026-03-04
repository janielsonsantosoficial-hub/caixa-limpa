import os
from google_auth_oauthlib.flow import Flow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify"
]

CLIENT_SECRETS_FILE = os.path.join(os.path.dirname(__file__), "client_secret.json")

def build_flow():
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri="http://127.0.0.1:8000/auth/callback"
    )
    return flow