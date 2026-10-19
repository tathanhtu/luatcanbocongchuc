"""Voice Studio – chuyển văn bản thành giọng nói bằng ElevenLabs (duyệt/nghe thử/chọn giọng, tải MP3).

Chạy:  pip install -r requirements.txt && python app.py   →   http://127.0.0.1:5050
Khoá API ElevenLabs nhập trực tiếp trên trang (hoặc đặt biến môi trường ELEVENLABS_API_KEY).
"""
import io
import os
import re

import requests
from flask import Flask, jsonify, request, send_file, send_from_directory

from vnnorm import normalize

API = "https://api.elevenlabs.io/v1"
app = Flask(__name__, static_folder="static")


class ElError(Exception):
    def __init__(self, status, msg):
        self.status, self.msg = status, msg


@app.errorhandler(ElError)
def on_el_error(e):
    return jsonify(error=e.msg), e.status


def api_key():
    k = request.headers.get("X-Key") or os.getenv("ELEVENLABS_API_KEY")
    if not k:
        raise ElError(401, "Chưa có khoá API ElevenLabs. Hãy dán khoá vào ô ở đầu trang.")
    return k.strip()


def el(method, path, **kw):
    try:
        r = requests.request(method, API + path, headers={"xi-api-key": api_key()}, timeout=180, **kw)
    except requests.RequestException as e:
        raise ElError(502, f"Không kết nối được ElevenLabs: {e}")
    if not r.ok:
        msg = r.text[:300]
        try:
            d = r.json().get("detail")
            msg = d.get("message") if isinstance(d, dict) else (d if isinstance(d, str) else str(d))
        except Exception:
            pass
        raise ElError(r.status_code, f"ElevenLabs: {msg}")
    return r


def labels_of(v):
    keys = ["gender", "age", "accent", "descriptive", "use_case", "language"]
    src = {**(v.get("labels") or {}), **{k: v.get(k) for k in keys if v.get(k)}}
    return [str(src[k]) for k in keys if src.get(k)]


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/quota")
def quota():
    d = el("GET", "/user/subscription").json()
    return jsonify(used=d.get("character_count"), limit=d.get("character_limit"), tier=d.get("tier"))


@app.get("/api/voices")
def voices():
    source, q = request.args.get("source", "mine"), request.args.get("q", "").strip()
    if source == "library":
        params = {"page_size": 40}
        if q:
            params["search"] = q
        lang = request.args.get("language", "vi")
        if lang != "all":
            params["language"] = lang
        items = el("GET", "/shared-voices", params=params).json().get("voices", [])
        return jsonify(voices=[dict(id=v["voice_id"], owner=v.get("public_owner_id"), name=v.get("name"),
                                    preview=v.get("preview_url"), labels=labels_of(v), library=True) for v in items])
    items = el("GET", "/voices").json().get("voices", [])
    ql = q.lower()
    out = [dict(id=v["voice_id"], name=v.get("name"), preview=v.get("preview_url"), labels=labels_of(v),
                category=v.get("category")) for v in items]
    if ql:
        out = [v for v in out if ql in (v["name"] or "").lower() or any(ql in l.lower() for l in v["labels"])]
    return jsonify(voices=out)


@app.post("/api/voices/add")
def add_voice():
    d = request.get_json(force=True)
    r = el("POST", f"/voices/add/{d['owner']}/{d['voice_id']}", json={"new_name": d.get("name") or "Giọng mới"})
    return jsonify(voice_id=r.json().get("voice_id", d["voice_id"]))


@app.post("/api/normalize")
def norm():
    return jsonify(text=normalize((request.get_json(force=True).get("text") or "")))


def chunks_of(text, limit=2500):
    sents = [s.strip() for s in re.findall(r"[^.!?…\n]+(?:[.!?…]+|\n|$)", text) if s.strip()]
    out, cur = [], ""
    for s in sents:
        if cur and len(cur) + len(s) + 1 > limit:
            out.append(cur)
            cur = ""
        cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


@app.post("/api/speak")
def speak():
    d = request.get_json(force=True)
    text = (d.get("text") or "").strip()
    if not text:
        raise ElError(400, "Chưa có văn bản")
    if d.get("normalize", True):
        text = normalize(text)
    voice, model = d.get("voice_id"), d.get("model") or "eleven_multilingual_v2"
    if not voice:
        raise ElError(400, "Hãy chọn một giọng đọc")
    s = d.get("settings") or {}
    if model == "eleven_v3":
        stab = min((0.0, 0.5, 1.0), key=lambda x: abs(x - float(s.get("stability", 0.5))))
        vs = {"stability": stab}
    else:
        vs = {"stability": float(s.get("stability", 0.4)), "similarity_boost": float(s.get("similarity", 0.8)),
              "style": float(s.get("style", 0.3)), "use_speaker_boost": bool(s.get("boost", True)),
              "speed": float(s.get("speed", 1.0))}
    parts, audio = chunks_of(text), b""
    for i, p in enumerate(parts):
        body = {"text": p, "model_id": model, "voice_settings": vs}
        if i > 0:
            body["previous_text"] = parts[i - 1][-300:]
        if i < len(parts) - 1:
            body["next_text"] = parts[i + 1][:300]
        if model in ("eleven_flash_v2_5", "eleven_turbo_v2_5"):
            body["language_code"] = "vi"
        audio += el("POST", f"/text-to-speech/{voice}", params={"output_format": "mp3_44100_128"}, json=body).content
    return send_file(io.BytesIO(audio), mimetype="audio/mpeg", download_name="giong-noi.mp3")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)
