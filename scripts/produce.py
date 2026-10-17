"""One fact-checked narrated Short per run using Higgsfield API and OpenAI TTS.

Live generation requires explicit owner API keys and a production enable switch.
No text-fact generation: every spoken word and video scene is prewritten in stories.json.

--dry-run uses no credentials, does not incur charges and does not upload.
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
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from safety import require_authorization

from publish import CHANNEL_ID, eligible, uploaded_markers, uploads_playlist, upload_file, youtube_service

ROOT = pathlib.Path(__file__).resolve().parents[1]
HF_ENDPOINT = "https://api.higgsfield.ai/alibaba/wan-3.0/text-to-video"
HF_STATUS = "https://api.higgsfield.ai/requests/{request_id}/status"
HF_USD_PER_SECOND_UPPER = 0.10  # 720p documented price as of Oct 2026; estimates are not billing ceilings
TTS_BUDGET_RESERVE_USD = 0.75
MAX_SCENES = 4
MAX_SECONDS_PER_SCENE = 8


def load_stories(file: pathlib.Path) -> list[dict]:
    stories = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(stories, list):
        raise ValueError("Stories must be a JSON array")
    seen = set()
    for story in stories:
        if story["id"] in seen:
            raise ValueError("Duplicate story id")
        seen.add(story["id"])
        if not re.fullmatch("[a-z0-9][a-z0-9-]{3,80}", story["id"]):
            raise ValueError("Bad story id")
        if len(story["title"]) > 100:
            raise ValueError("Title too long")
        if story.get("approved") not in (True, False) or story.get("rights_confirmed") is not True:
            raise ValueError("Stories must be explicitly vetted with rights_confirmed:true")
        if not story.get("narration") or not story.get("source_urls") or not story.get("shots"):
            raise ValueError("Story requires narration, sources and shots")
        if len(story["narration"]) > 1100:
            raise ValueError("Narration too long for Short")
        if not 2 <= len(story["shots"]) <= MAX_SCENES:
            raise ValueError("Story must have between 2 and 4 different scenes")
        for shot in story["shots"]:
            if not 4 <= shot["duration"] <= MAX_SECONDS_PER_SCENE:
                raise ValueError("Unsupported scene duration")
            if len(shot["prompt"]) > 1000 or len(shot["caption"]) > 45:
                raise ValueError("Prompt or caption too long")
        for source in story["source_urls"]:
            if urlparse(source).scheme != "https":
                raise ValueError("Only https source references")
    return stories


def estimated_upper_cost(story: dict) -> float:
    return round(sum(shot["duration"] for shot in story["shots"]) * HF_USD_PER_SECOND_UPPER + TTS_BUDGET_RESERVE_USD, 2)


def choose_story(stories: list[dict], uploaded: set[str], now: datetime) -> dict | None:
    for story in stories:
        if eligible(story, now) and story["id"] not in uploaded:
            return story
    return None


def _hf_headers():
    key = os.environ.get("HF_API_KEY", "")
    if not key:
        raise RuntimeError("Falta HF_API_KEY en GitHub Secrets")
    # Complete credential copied from Higgsfield API Console, not an MCP OAuth token.
    return {"Authorization": "Key " + key, "Content-Type": "application/json"}


def extract_video_url(response: dict) -> str:
    def geturl(obj):
        if isinstance(obj, str) and obj.startswith("https://"):
            return obj
        if isinstance(obj, dict):
            url = obj.get("url") or obj.get("video_url")
            if isinstance(url, str) and url.startswith("https://"):
                return url
        return None
    candidates = [response.get("video"), response.get("result", {}).get("video") if isinstance(response.get("result"), dict) else None]
    for thing in candidates:
        found = geturl(thing)
        if found:
            return found
    raise RuntimeError("Higgsfield reported completion without a downloadable video URL")


def hf_render(prompt: str, duration: int, part: int) -> str:
    require_authorization(production=True)
    body = {
        "prompt": prompt,
        "duration": duration,
        "resolution": "720p",
        "aspect_ratio": "9:16",
        "generate_audio": False,
        "enable_thinking": False,
    }
    # Never retry a POST automatically; timeout could mean generation was accepted/charged.
    try:
        result = requests.post(HF_ENDPOINT, headers=_hf_headers(), json=body, timeout=60)
        result.raise_for_status()
        data = result.json()
    except requests.RequestException as exc:
        raise RuntimeError("Higgsfield submission uncertain; STOP and check console before retry") from exc
    request_id = data.get("request_id")
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", request_id):
        raise RuntimeError("No usable request_id from Higgsfield")
    print("Higgsfield scene", part, "submitted; request_id", request_id, flush=True)
    # Polling does not create more generations and is safe to retry.
    for _ in range(80):
        time.sleep(6)
        try:
            resp = requests.get(HF_STATUS.format(request_id=request_id), headers=_hf_headers(), timeout=30)
            resp.raise_for_status()
            status_data = resp.json()
        except requests.RequestException:
            continue
        status = status_data.get("status")
        if status == "completed":
            return extract_video_url(status_data)
        if status in {"failed", "nsfw", "canceled"}:
            raise RuntimeError(f"Generation failed ({status}) for scene {part}; request_id {request_id}")
    raise RuntimeError(f"Generation still pending; request_id {request_id}. Do not resubmit until verified")


def download_result(url: str, path: pathlib.Path) -> None:
    parts = urlparse(url)
    if parts.scheme != "https" or not parts.hostname or parts.hostname in {"localhost", "127.0.0.1"}:
        raise ValueError("Unsafe media result URL")
    size = 0
    with requests.get(url, stream=True, timeout=(15,90), allow_redirects=False) as response:
        response.raise_for_status()
        with path.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1_048_576):
                if chunk:
                    size += len(chunk)
                    if size > 200 * 1024 * 1024:
                        raise RuntimeError("Scene too large")
                    f.write(chunk)
    if size < 100000:
        raise RuntimeError("Invalid tiny scene")


def synthesize_voice(story: dict, path: pathlib.Path):
    require_authorization(production=True)
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Falta OPENAI_API_KEY; la cuenta ChatGPT no incluye saldo de API")
    payload = {
        "model": "gpt-4o-mini-tts",
        "voice": "marin",
        "input": story["narration"],
        "instructions": "Español de España, voz clara, intrigante y natural, ritmo ágil documental; evita dramatización exagerada.",
        "response_format": "mp3",
    }
    with requests.post("https://api.openai.com/v1/audio/speech", headers={"Authorization": "Bearer "+key}, json=payload, stream=True, timeout=(20,100)) as resp:
        resp.raise_for_status()
        with path.open("wb") as f:
            for chunk in resp.iter_content(65536):
                f.write(chunk)


def execute(command: list[str]):
    run = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=200)
    if run.returncode:
        raise RuntimeError("ffmpeg/ffprobe error; last lines: " + run.stderr[-1600:].decode("utf-8", "replace"))
    return run.stdout.decode().strip()


def duration_of(file: pathlib.Path) -> float:
    return float(execute(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(file)]))


def overlay_file(title: str, file: pathlib.Path) -> None:
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (720,1280), (0,0,0,0))
    draw=ImageDraw.Draw(img)
    for y in range(920,1280):
        alpha=int(185 * (y-920)/360)
        draw.line(((0,y),(720,y)), fill=(4, 9, 21, alpha))
    draw.rounded_rectangle((26, 34, 335, 83), radius=12, fill=(0,0,0,140))
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size=35)
        small=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size=20)
    except OSError:
        font = ImageFont.load_default(); small = ImageFont.load_default()
    draw.text((38,43),"LaRedColect · IA", font=small, fill=(245,245,245,255))
    words=title.split(); lines=[]; line=""
    for word in words:
        candidate=(line+" "+word).strip()
        if draw.textbbox((0,0),candidate,font=font)[2] > 650 and line:
            lines.append(line); line=word
        else: line=candidate
    if line: lines.append(line)
    for i,line in enumerate(lines[:3]):
        draw.text((36,1050+i*55),line,font=font,fill=(255,239,204,255),stroke_width=2,stroke_fill=(0,0,0,235))
    img.save(file)


def produce_video(story: dict, directory: pathlib.Path) -> pathlib.Path:
    voice = directory / "narration.mp3"
    synthesize_voice(story, voice)
    seconds = duration_of(voice)
    shots_total = sum(x["duration"] for x in story["shots"])
    if seconds > shots_total+8:
        raise RuntimeError("Narration too long for 4 scenes; shorten the approved script")
    files=[]
    for idx,shot in enumerate(story["shots"], 1):
        url=hf_render(shot["prompt"], shot["duration"], idx)
        raw = directory / f"scene-{idx}-raw.mp4"
        ready = directory / f"scene-{idx}.mp4"
        art = directory / f"overlay-{idx}.png"
        download_result(url,raw)
        overlay_file(shot["caption"],art)
        execute([
            "ffmpeg","-hide_banner","-loglevel","error","-y",
            "-i",str(raw),"-loop","1","-framerate","30","-i",str(art),
            "-filter_complex","[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=30,tpad=stop_mode=clone:stop_duration=2[v];[v][1:v]overlay=0:0:shortest=1[out]",
            "-map","[out]","-t",str(shot["duration"]),"-an", "-pix_fmt","yuv420p", "-c:v","libx264","-preset","veryfast","-crf","23", str(ready)
        ])
        files.append(ready)
    lines = directory / "concat.txt"
    lines.write_text("".join("file '"+str(f)+"'\n" for f in files), encoding="utf-8")
    final = directory / "final.mp4"
    target_len = seconds + 0.8
    execute([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lines),
        "-i", str(voice), "-filter_complex",
        f"[0:v]tpad=stop_mode=clone:stop_duration=9,trim=duration={target_len:.3f},setpts=PTS-STARTPTS[v];[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-t", f"{target_len:.3f}", str(final)
    ])
    return final


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--preview-only", action="store_true", help="Generate approved story but do not upload")
    parser.add_argument("--stories", default=str(ROOT / "content" / "stories.json"))
    args = parser.parse_args()
    stories = load_stories(pathlib.Path(args.stories))
    if args.dry_run:
        for x in stories:
            print(x["id"], "APPROVED" if x["approved"] else "NOT APPROVED", "estimated $", estimated_upper_cost(x))
        print("DRY RUN OK: no paid calls or uploads")
        return
    require_authorization(production=True)
    if os.environ.get("LRC_PRODUCTION_ENABLED") != "true":
        raise RuntimeError("Production inactive: explicitly enable the LRC_PRODUCTION_ENABLED GitHub variable")
    if not os.environ.get("HF_API_KEY") or not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("Missing HF_API_KEY or OPENAI_API_KEY; STOP without spending")
    yt = youtube_service()  # Fail before paid generation if YouTube is unlinked.
    markers = uploaded_markers(yt, uploads_playlist(yt))
    story=choose_story(stories, markers, datetime.now(timezone.utc))
    if not story:
        print("No new vetted stories due. Zero generation spend.")
        return
    cost=estimated_upper_cost(story)
    budget=float(os.environ.get("MAX_ESTIMATED_VIDEO_USD", "5"))
    if cost > budget:
        raise RuntimeError(f"Estimated video price ${cost:.2f} exceeds ${budget:.2f} cap")
    if story["privacy"] not in {"private", "unlisted", "public"}:
        raise RuntimeError("Invalid story visibility")
    print("Processing vetted:", story["id"], "price estimate up to $", cost)
    with tempfile.TemporaryDirectory(prefix="lrc-produce-") as td:
        target = produce_video(story, pathlib.Path(td))
        from publish import validate_entry
        meta = {"id":story["id"],"title":story["title"], "description":story["description"],
                "approved":True,"rights_confirmed":True,"video_url":"generated-local",
                "privacy":story["privacy"], "synthetic_media":story["synthetic_media"], "category_id":story.get("category_id","28")}
        validate_entry(meta)
        if args.preview_only:
            print("PREVIEW generated locally but not uploaded; output is ephemeral in CI")
            return
        # Marker recheck (also guards against repeated workflow starts).
        if story["id"] in uploaded_markers(yt, uploads_playlist(yt)):
            print("Already uploaded by another run; no duplicate")
            return
        url=upload_file(yt, meta, target)
        print("PUBLISHED:", url)

if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, AssertionError) as exc:
        print("ERROR:", str(exc), file=sys.stderr)
        sys.exit(1)
