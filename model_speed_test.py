"""Find a fast Gemini model for your key.

Run:  python model_speed_test.py
(GEMINI_API_KEY must be set in this Command Prompt window.)
"""
import os
import time

from google import genai
from google.genai import types

key = os.environ.get("GEMINI_API_KEY")
if not key:
    raise SystemExit("Set GEMINI_API_KEY first.")

client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=60000))

skip = ("image", "tts", "live", "audio", "embedding", "robotics", "computer")
names = []
for m in client.models.list():
    n = m.name.replace("models/", "")
    actions = getattr(m, "supported_actions", None) or []
    if "flash" in n and not any(w in n for w in skip) and (not actions or "generateContent" in actions):
        names.append(n)

print(f"Found {len(names)} flash models. Testing up to 8 (60s limit each)...\n")
for n in names[:8]:
    t = time.time()
    try:
        r = client.models.generate_content(model=n, contents="Reply with the single word: ready")
        print(f"{n:42} OK   {time.time() - t:5.1f}s  -> {r.text.strip()[:20]}")
    except Exception as err:
        print(f"{n:42} FAIL {time.time() - t:5.1f}s  {str(err)[:70]}")
