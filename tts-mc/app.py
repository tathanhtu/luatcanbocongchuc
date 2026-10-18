"""Phần mềm chuyển văn bản thành giọng nói tiếng Việt – phong cách MC ấm áp, truyền cảm.

Chạy:  pip install -r requirements.txt && python app.py
Mở:    http://127.0.0.1:5000

Dùng giọng thần kinh (Neural) của Microsoft Edge qua thư viện edge-tts (cần Internet).
Điểm khác biệt: văn bản được tách theo câu, mỗi câu được chỉnh tốc độ / cao độ / âm lượng
theo dấu câu (hỏi, cảm thán, ngắt nghỉ...) và chèn khoảng lặng để đọc giống MC.
"""
import asyncio
import io
import os
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache

import edge_tts
import requests
from flask import Flask, jsonify, request, send_file, send_from_directory

app = Flask(__name__, static_folder="static")

# Giọng "người thật": dùng API trả phí (đặt khoá API qua biến môi trường). Giọng Edge miễn phí làm dự phòng.
#   OPENAI_API_KEY      -> gpt-4o-mini-tts, nhận chỉ dẫn diễn đạt (ấm áp, truyền cảm...)
#   ELEVENLABS_API_KEY  -> eleven_multilingual_v2, chỉnh độ ổn định / phong cách
VOICES = {
    "coral": dict(provider="openai", id="coral", label="Coral – nữ, ấm áp, thân thiện"),
    "sage": dict(provider="openai", id="sage", label="Sage – nữ, điềm đạm, sang trọng"),
    "ash": dict(provider="openai", id="ash", label="Ash – nam, trầm ấm, tự nhiên"),
    "onyx": dict(provider="openai", id="onyx", label="Onyx – nam, trầm dày, mạnh mẽ"),
    "el_bella": dict(provider="eleven", id=os.getenv("EL_VOICE_F", "EXAVITQu4vr4xnSDxMaL"), label="ElevenLabs – nữ, mượt, giàu cảm xúc"),
    "el_adam": dict(provider="eleven", id=os.getenv("EL_VOICE_M", "pNInz6obpgDQGcFmaJgB"), label="ElevenLabs – nam, sâu, chuyên nghiệp"),
    "hoaimy": dict(provider="edge", id="vi-VN-HoaiMyNeural", label="Hoài My – nữ (miễn phí, Edge)"),
    "namminh": dict(provider="edge", id="vi-VN-NamMinhNeural", label="Nam Minh – nam (miễn phí, Edge)"),
}
KEY_ENV = {"openai": "OPENAI_API_KEY", "eleven": "ELEVENLABS_API_KEY", "edge": None}

# Chỉ dẫn diễn đạt cho gpt-4o-mini-tts theo phong cách
INSTRUCTIONS = {
    "mc_am_ap": "Bạn là MC chuyên nghiệp người Việt. Giọng ấm áp, gần gũi, giàu cảm xúc; nhấn nhá tự nhiên, ngắt hơi đúng chỗ, lên giọng khi hỏi, rộn ràng khi cảm thán, chậm rãi trân trọng ở ý quan trọng. Phát âm chuẩn tiếng Việt, không đọc như máy.",
    "ban_tin": "Phát thanh viên truyền hình chuyên nghiệp: rõ ràng, trang trọng, tin cậy, nhịp đều, ngữ điệu tiếng Việt tự nhiên.",
    "ke_chuyen": "Người kể chuyện tâm tình, chậm rãi, trầm ấm, có nhịp nghỉ và cảm xúc theo diễn biến câu chuyện, ngữ điệu tiếng Việt tự nhiên.",
    "soi_dong": "MC sự kiện sôi động, hào hứng, năng lượng cao nhưng vẫn tự nhiên và rõ ràng, ngữ điệu tiếng Việt chuẩn.",
}
# ElevenLabs: (stability, style)
EL_STYLE = {"mc_am_ap": (0.35, 0.45), "ban_tin": (0.6, 0.1), "ke_chuyen": (0.4, 0.5), "soi_dong": (0.25, 0.65)}

# Phong cách: (tốc độ %, cao độ Hz, hệ số nhấn câu hỏi/cảm thán, nghỉ cuối câu, nghỉ dấu phẩy/ngoặc ms)
STYLES = {
    "mc_am_ap": dict(label="MC ấm áp, truyền cảm", rate=-8, pitch=-2, q_pitch=6, ex_pitch=5, ex_rate=4, end_pause=520, comma_pause=220),
    "ban_tin": dict(label="Bản tin trang trọng", rate=-3, pitch=-4, q_pitch=3, ex_pitch=2, ex_rate=0, end_pause=420, comma_pause=160),
    "ke_chuyen": dict(label="Kể chuyện chậm rãi", rate=-16, pitch=-1, q_pitch=5, ex_pitch=4, ex_rate=2, end_pause=750, comma_pause=300),
    "soi_dong": dict(label="Sôi động, hào hứng", rate=6, pitch=3, q_pitch=6, ex_pitch=8, ex_rate=8, end_pause=320, comma_pause=140),
}

SENT_RE = re.compile(r"[^.!?…\n]+(?:[.!?…]+|\n|$)", re.S)


def split_sentences(text: str):
    """Tách câu, giữ dấu kết câu để suy ra ngữ điệu."""
    out = []
    for m in SENT_RE.finditer(text.replace("\r", "")):
        s = m.group(0).strip()
        if s:
            out.append(s)
    return out


def split_clauses(sentence: str):
    """Tách một câu dài thành các mệnh đề theo dấu phẩy / chấm phẩy / hai chấm để ngắt hơi."""
    parts = re.split(r"(?<=[,;:])\s+", sentence)
    return [p for p in parts if p.strip()]


def fmt(v, unit):
    return f"{int(round(v)):+d}{unit}"


@lru_cache(maxsize=32)
def silence(ms: int) -> bytes:
    """MP3 im lặng 24kHz mono (cần ffmpeg); không có ffmpeg thì bỏ qua."""
    if not shutil.which("ffmpeg") or ms <= 0:
        return b""
    r = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
         "-t", f"{ms / 1000:.3f}", "-c:a", "libmp3lame", "-b:a", "48k", "-f", "mp3", "-"],
        capture_output=True,
    )
    return r.stdout if r.returncode == 0 else b""


async def synth_piece(text, voice, rate, pitch, volume):
    comm = edge_tts.Communicate(text, voice, rate=fmt(rate, "%"), pitch=fmt(pitch, "Hz"), volume=fmt(volume, "%"))
    buf = io.BytesIO()
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    return buf.getvalue()


async def synth(text, voice, style, rate, pitch, volume, expressive):
    st = STYLES[style]
    base_rate = st["rate"] + rate
    base_pitch = st["pitch"] + pitch
    jobs, pauses = [], []
    sentences = split_sentences(text)
    for si, sent in enumerate(sentences):
        end = sent.rstrip()[-1:]
        p_add = r_add = 0
        if expressive:
            if end == "?":
                p_add = st["q_pitch"]
            elif end == "!":
                p_add, r_add = st["ex_pitch"], st["ex_rate"]
            elif end == "…":
                r_add = -6
        clauses = split_clauses(sent)
        for ci, cl in enumerate(clauses):
            last = ci == len(clauses) - 1
            # Cuối câu hạ giọng nhẹ để nghe "chốt ý"; các mệnh đề giữa câu nâng nhẹ.
            drift = 0
            if expressive:
                drift = -2 if last and end in ".…" else (1 if not last else 0)
            jobs.append(synth_piece(cl, voice, base_rate + r_add, base_pitch + p_add + drift, volume))
            if last:
                extra = 120 if end in "!?" else 0
                pauses.append(st["end_pause"] + extra if si < len(sentences) - 1 else 0)
            else:
                pauses.append(st["comma_pause"])
    audios = await asyncio.gather(*jobs)
    out = io.BytesIO()
    for a, ms in zip(audios, pauses):
        out.write(a)
        if expressive:
            out.write(silence(ms))
    return out.getvalue()


def chunk_text(text, limit=2800):
    out, cur = [], ""
    for sent in split_sentences(text):
        if cur and len(cur) + len(sent) + 1 > limit:
            out.append(cur)
            cur = ""
        cur = f"{cur} {sent}".strip()
    if cur:
        out.append(cur)
    return out


def speed_of(rate):
    return max(0.7, min(1.3, 1 + rate / 100))


def synth_openai(text, voice, style, rate):
    audio = b""
    for part in chunk_text(text):
        r = requests.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            json=dict(model="gpt-4o-mini-tts", voice=voice, input=part, instructions=INSTRUCTIONS[style],
                      response_format="mp3", speed=speed_of(rate)),
            timeout=120,
        )
        r.raise_for_status()
        audio += r.content
    return audio


def synth_eleven(text, voice_id, style, rate):
    stability, st = EL_STYLE[style]
    audio = b""
    for part in chunk_text(text, 2500):
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128",
            headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
            json=dict(text=part, model_id="eleven_multilingual_v2", language_code="vi",
                      voice_settings=dict(stability=stability, similarity_boost=0.8, style=st,
                                          use_speaker_boost=True, speed=speed_of(rate))),
            timeout=120,
        )
        r.raise_for_status()
        audio += r.content
    return audio


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/options")
def options():
    return jsonify(
        voices=[{"key": k, "label": v["label"] + ("" if not KEY_ENV[v["provider"]] or os.getenv(KEY_ENV[v["provider"]]) else " – cần API key"),
                 "ready": not KEY_ENV[v["provider"]] or bool(os.getenv(KEY_ENV[v["provider"]]))} for k, v in VOICES.items()],
        styles=[{"key": k, "label": v["label"]} for k, v in STYLES.items()],
    )


@app.post("/api/speak")
def speak():
    d = request.get_json(force=True)
    text = (d.get("text") or "").strip()
    if not text:
        return jsonify(error="Chưa có văn bản"), 400
    if len(text) > 20000:
        return jsonify(error="Văn bản quá dài (tối đa 20.000 ký tự)"), 400
    v = VOICES.get(d.get("voice"), VOICES["coral"])
    voice = v["id"]
    env = KEY_ENV[v["provider"]]
    if env and not os.getenv(env):
        return jsonify(error=f"Giọng này cần khoá API. Hãy đặt biến môi trường {env} rồi chạy lại."), 400
    style = d.get("style") if d.get("style") in STYLES else "mc_am_ap"
    clamp = lambda v, lo, hi: max(lo, min(hi, int(v or 0)))
    try:
        rate = clamp(d.get("rate"), -50, 50)
        if v["provider"] == "openai":
            audio = synth_openai(text, voice, style, rate + STYLES[style]["rate"])
        elif v["provider"] == "eleven":
            audio = synth_eleven(text, voice, style, rate + STYLES[style]["rate"])
        else:
            audio = asyncio.run(synth(
                text, voice, style, rate, clamp(d.get("pitch"), -30, 30), clamp(d.get("volume"), -50, 50),
                bool(d.get("expressive", True)),
            ))
    except Exception as e:  # lỗi mạng / dịch vụ
        return jsonify(error=f"Không tạo được giọng nói: {e}. Hãy kiểm tra kết nối Internet."), 502
    if not audio:
        return jsonify(error="Dịch vụ không trả về âm thanh"), 502
    return send_file(io.BytesIO(audio), mimetype="audio/mpeg", download_name="giong-noi.mp3")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
