"""One-time desktop OAuth setup. Save GitHub secrets locally; never paste them into chat."""
import argparse
import json
import pathlib
import stat
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly", "https://www.googleapis.com/auth/youtube.upload"]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("client_secret_json", help="Downloaded OAuth desktop JSON file")
    args = parser.parse_args()
    cfg = json.loads(pathlib.Path(args.client_secret_json).read_text(encoding="utf-8"))
    flow = InstalledAppFlow.from_client_config(cfg, scopes=SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    data = cfg.get("installed", {})
    if not creds.refresh_token:
        raise SystemExit("No refresh token returned; check OAuth consent app and retry")
    root = pathlib.Path(__file__).resolve().parents[1]
    output = root / ".local" / "youtube-github-secrets.txt"
    output.parent.mkdir(exist_ok=True)
    values = {
        "YOUTUBE_CLIENT_ID": data["client_id"],
        "YOUTUBE_CLIENT_SECRET": data["client_secret"],
        "YOUTUBE_REFRESH_TOKEN": creds.refresh_token,
    }
    output.write_text("\n".join(f"{k}={v}" for k,v in values.items())+"\n", encoding="utf-8")
    output.chmod(stat.S_IRUSR | stat.S_IWUSR)
    print("Acceso concedido. Secretos guardados SOLO en:", output)
    print("Copiar los tres valores en GitHub Settings > Secrets and variables > Actions.")
    print("No subir el archivo a GitHub, no enviarlo por email ni pegarlo en el chat.")
