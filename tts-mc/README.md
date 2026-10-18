# Giọng đọc MC (Text-to-Speech tiếng Việt)

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...        # giọng Coral, Sage, Ash, Onyx (gpt-4o-mini-tts, có chỉ dẫn diễn đạt MC)
export ELEVENLABS_API_KEY=...       # tuỳ chọn: 2 giọng ElevenLabs (đổi bằng EL_VOICE_F / EL_VOICE_M)
python app.py                       # mở http://127.0.0.1:5000
```
- 4 giọng OpenAI + 2 giọng ElevenLabs là giọng "người thật"; hai giọng Hoài My / Nam Minh (Edge) miễn phí, không cần khoá nhưng nghe còn hơi máy.
- `ffmpeg` (tuỳ chọn) chỉ dùng để chèn khoảng nghỉ cho giọng Edge.
