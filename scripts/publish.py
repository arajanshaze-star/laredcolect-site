"""Secure, bounded, idempotent YouTube uploader for one LaRedColect video/run.

Secrets are read exclusively from the process environment. Never print OAuth data.
Run `python scripts/publish.py --dry-run` without credentials for a safe preflight.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlparse

from safety import require_authorization

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHANNEL_ID = "UCPV98kjwLUta0vuiy3fx64A"
MARKER = "LRC-ID:"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.upload",
]
ALLOWED_VIDEO_HOSTS = {"d2ol7oe51mr4n9.cloudfront.net"}
MAX_VIDEO_BYTES = 300 * 1024 * 1024


def validate_entry(entry: dict) -> None:
    assert isinstance(entry, dict), "Entry must be a JSON object"
    assert re.fullmatch(r"[a-z0-9][a-z0-9-]{3,80}", str(entry.get("id", ""))), "Invalid ID"
    assert isinstance(entry.get("title"), str) and 3 <= len(entry["title"]) <= 100, "Invalid title"
    assert isinstance(entry.get("description"), str) and len(entry["description"]) <= 4500, "Invalid description"
    assert entry.get("privacy", "private") in {"private", "unlisted", "public"}, "Invalid privacy"
    assert entry.get("approved") in {True, False}, "approved must be an explicit boolean"
    assert entry.get("synthetic_media") in {True, False}, "synthetic_media must be explicit boolean"
    assert entry.get("rights_confirmed") is True, "Rights must be confirmed before scheduling"
    assert isinstance(entry.get("video_url"), str) and entry["video_url"], "Missing video URL"
    if entry.get("publish_after"):
        iso = entry["publish_after"].replace("Z", "+00:00")
        assert datetime.fromisoformat(iso).tzinfo, "publish_after must have timezone"
    if entry.get("category_id"):
        assert str(entry["category_id"]).isdigit(), "Invalid category_id"


def load_queue(path: pathlib.Path) -> list[dict]:
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("Queue JSON must be an array")
    seen = set()
    for entry in entries:
        validate_entry(entry)
        if entry["id"] in seen:
            raise ValueError("Duplicate queue ID " + entry["id"])
        seen.add(entry["id"])
    return entries


def eligible(entry: dict, now: datetime | None = None) -> bool:
    if entry["approved"] is not True:
        return False
    if entry.get("publish_after"):
        when = datetime.fromisoformat(entry["publish_after"].replace("Z", "+00:00"))
        if when > (now or datetime.now(timezone.utc)):
            return False
    return True


def youtube_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    required = ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError("Faltan secrets GitHub: " + ", ".join(missing))
    creds = Credentials(
        None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds


def youtube_service():
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=youtube_credentials(), cache_discovery=False)


def uploads_playlist(yt):
    items = yt.channels().list(part="contentDetails", mine=True).execute().get("items", [])
    if len(items) != 1 or items[0].get("id") != CHANNEL_ID:
        raise RuntimeError("Canal OAuth distinto de LaRedColect; subida detenida")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def uploaded_markers(yt, playlist_id: str) -> set[str]:
    """Scan up to 1000 uploads. The channel currently has far fewer videos."""
    markers = set()
    page = None
    for _ in range(20):
        kwargs = dict(part="snippet", playlistId=playlist_id, maxResults=50)
        if page:
            kwargs["pageToken"] = page
        response = yt.playlistItems().list(**kwargs).execute()
        ids = [item.get("snippet", {}).get("resourceId", {}).get("videoId") for item in response.get("items", [])]
        ids = [id for id in ids if id]
        if ids:
            detail = yt.videos().list(part="snippet", id=",".join(ids)).execute()
            for v in detail.get("items", []):
                desc = v.get("snippet", {}).get("description", "")
                markers.update(re.findall(r"\bLRC-ID:([a-z0-9-]+)\b", desc))
        page = response.get("nextPageToken")
        if not page:
            break
    else:
        raise RuntimeError("More than 1000 uploads; refuse upload until a durable ledger is configured")
    return markers


def ensure_media_file(file: pathlib.Path) -> None:
    if not file.is_file() or file.stat().st_size < 100_000 or file.stat().st_size > MAX_VIDEO_BYTES:
        raise RuntimeError("Video missing, too small or too large")
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name", "-of", "default=nw=1:nk=1", str(file)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode or not result.stdout.strip():
        raise RuntimeError("Input does not contain a valid video stream")


def download_video(url: str, target: pathlib.Path) -> None:
    from requests import get
    parts = urlparse(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_VIDEO_HOSTS or parts.username or parts.password:
        raise ValueError("Video source must use approved HTTPS Higgsfield CDN")
    with get(url, stream=True, allow_redirects=False, timeout=(15, 90)) as response:
        response.raise_for_status()
        if 300 <= response.status_code < 400:
            raise RuntimeError("Redirect blocked")
        size = 0
        with target.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024*1024):
                if chunk:
                    size += len(chunk)
                    if size > MAX_VIDEO_BYTES:
                        raise RuntimeError("Video exceeds max size")
                    f.write(chunk)
    ensure_media_file(target)


def upload_file(yt, entry: dict, file: pathlib.Path) -> str:
    require_authorization()
    from googleapiclient.http import MediaFileUpload
    ensure_media_file(file)
    description = entry["description"].strip() + "\n\n" + MARKER + entry["id"]
    status = {
        "privacyStatus": entry["privacy"],
        "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": entry["synthetic_media"],
    }
    body = {
        "snippet": {
            "title": entry["title"],
            "description": description,
            "categoryId": str(entry.get("category_id", "28")),
            "defaultLanguage": "es",
        },
        "status": status,
    }
    request = yt.videos().insert(
        part="snippet,status", body=body,
        media_body=MediaFileUpload(str(file), mimetype="video/mp4", chunksize=4*1024*1024, resumable=True),
    )
    video = None
    while video is None:
        _, video = request.next_chunk()
    if not video or not video.get("id"):
        raise RuntimeError("YouTube did not return a video ID")
    return "https://youtu.be/" + video["id"]


def run_queue(yt, entries: list[dict], now: datetime | None = None) -> str | None:
    playlist = uploads_playlist(yt)
    uploaded = uploaded_markers(yt, playlist)
    for entry in entries:
        if not eligible(entry, now):
            continue
        if entry["id"] in uploaded:
            print("Ya subido:", entry["id"])
            continue
        with tempfile.TemporaryDirectory(prefix="lrc-upload-") as td:
            file = pathlib.Path(td) / "upload.mp4"
            download_video(entry["video_url"], file)
            link = upload_file(yt, entry, file)
        print("Publicado:", link, "privacidad:", entry["privacy"])
        return link
    print("No hay vídeos nuevos aprobados y pendientes")
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", default=str(ROOT / "content" / "queue.json"))
    parser.add_argument("--dry-run", action="store_true", help="Offline metadata preflight, no network or uploads")
    args = parser.parse_args()
    entries = load_queue(pathlib.Path(args.queue))
    if args.dry_run:
        for entry in entries:
            print(entry["id"], "READY" if eligible(entry) else "WAIT", entry["privacy"], entry["title"])
        print("VALIDACION OK: sin API ni consumo")
        return
    require_authorization()
    run_queue(youtube_service(), entries)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, AssertionError) as exc:
        print("ERROR:", str(exc), file=sys.stderr)
        sys.exit(1)
