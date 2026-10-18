"""Phần mềm chuyển văn bản thành giọng nói tiếng Việt – phong cách MC ấm áp, truyền cảm.

Chạy:  pip install -r requirements.txt && python app.py
Mở:    http://127.0.0.1:5000

Dùng giọng thần kinh (Neural) của Microsoft Edge qua thư viện edge-tts (cần Internet).
Điểm khác biệt: văn bản được tách theo câu, mỗi câu được chỉnh tốc độ / cao độ / âm lượng
theo dấu câu (hỏi, cảm thán, ngắt nghỉ...) và chèn khoảng lặng để đọc giống MC.
"""
import asyncio
import io
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache

import edge_tts
from flask import Flask, jsonify, request, send_file, send_from_directory

app = Flask(__name__, static_folder="static")

VOICES = {
    "hoaimy": {"id": "vi-VN-HoaiMyNeural", "label": "Hoài My – nữ, ấm áp"},
    "namminh": {"id": "vi-VN-NamMinhNeural", "label": "Nam Minh – nam, trầm ấm"},
}

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


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/options")
def options():
    return jsonify(
        voices=[{"key": k, "label": v["label"]} for k, v in VOICES.items()],
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
    voice = VOICES.get(d.get("voice"), VOICES["hoaimy"])["id"]
    style = d.get("style") if d.get("style") in STYLES else "mc_am_ap"
    clamp = lambda v, lo, hi: max(lo, min(hi, int(v or 0)))
    try:
        audio = asyncio.run(synth(
            text, voice, style,
            clamp(d.get("rate"), -50, 50), clamp(d.get("pitch"), -30, 30), clamp(d.get("volume"), -50, 50),
            bool(d.get("expressive", True)),
        ))
    except Exception as e:  # lỗi mạng / dịch vụ
        return jsonify(error=f"Không tạo được giọng nói: {e}. Hãy kiểm tra kết nối Internet."), 502
    if not audio:
        return jsonify(error="Dịch vụ không trả về âm thanh"), 502
    return send_file(io.BytesIO(audio), mimetype="audio/mpeg", download_name="giong-noi.mp3")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
