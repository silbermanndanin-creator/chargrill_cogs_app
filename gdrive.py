"""Optional Google Drive upload for generated variation letters.

Headless (server) auth via a Google SERVICE ACCOUNT — no interactive login, which suits
Streamlit Cloud. To enable:
  1. In Google Cloud, create a project, enable the Drive API, make a Service Account, and
     download its JSON key.
  2. Share the target Drive folder with the service account's email (…@….iam.gserviceaccount.com)
     as **Editor**.
  3. Put the JSON in the app's Secrets as GDRIVE_SERVICE_ACCOUNT (and optionally GDRIVE_FOLDER_ID).

When not configured, is_configured() is False and the app simply hides the Drive button.
"""
import io
import json
import os

# The owner's "Variation letters" Drive folder (from the shared URL). Overridable via secret.
DEFAULT_FOLDER_ID = "14VyS0AdH2EUf3pZBAMAuuXOEsfs0Y9Nq"
# Full Drive scope: 'drive.file' only sees files the app itself created, so it can't write
# into a folder the owner created and shared (it 404s on the folder). 'drive' lets the
# service account write into any folder shared with it as Editor.
SCOPES = ["https://www.googleapis.com/auth/drive"]
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def folder_id():
    return os.environ.get("GDRIVE_FOLDER_ID") or DEFAULT_FOLDER_ID


def is_configured() -> bool:
    """True when a service-account key is present (so the Drive button should show)."""
    return bool((os.environ.get("GDRIVE_SERVICE_ACCOUNT") or "").strip())


def _service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    info = json.loads(os.environ["GDRIVE_SERVICE_ACCOUNT"])
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def service_account_email():
    """The client_email of the configured service account (share the folder with THIS)."""
    try:
        return json.loads(os.environ.get("GDRIVE_SERVICE_ACCOUNT") or "{}").get("client_email")
    except Exception:
        return None


def check_access(folder: str = None):
    """(ok, message) — verify the service account can actually see the target folder."""
    fid = folder or folder_id()
    try:
        meta = _service().files().get(fileId=fid, fields="id,name",
                                      supportsAllDrives=True).execute()
        return True, f"OK — folder '{meta.get('name')}' is accessible."
    except Exception as e:
        return False, str(e)


def upload_docx(filename: str, data: bytes, folder: str = None) -> dict:
    """Upload .docx bytes into the Drive folder; returns {'id', 'link'}. Raises on failure.
    If a file with the same name already exists in the folder, it's updated in place."""
    from googleapiclient.http import MediaIoBaseUpload
    svc = _service()
    fid = folder or folder_id()
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=DOCX_MIME, resumable=False)
    # Replace an existing same-named file so re-saving doesn't create duplicates.
    safe = filename.replace("'", "\\'")
    q = f"name = '{safe}' and '{fid}' in parents and trashed = false"
    try:
        existing = (svc.files().list(q=q, fields="files(id)", spaces="drive",
                                     supportsAllDrives=True,
                                     includeItemsFromAllDrives=True).execute().get("files") or [])
    except Exception:
        existing = []
    if existing:
        f = svc.files().update(fileId=existing[0]["id"], media_body=media,
                               fields="id,webViewLink", supportsAllDrives=True).execute()
    else:
        meta = {"name": filename, "parents": [fid]}
        f = svc.files().create(body=meta, media_body=media, fields="id,webViewLink",
                               supportsAllDrives=True).execute()
    return {"id": f.get("id"), "link": f.get("webViewLink")}
