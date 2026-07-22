#!/usr/bin/env python3
"""
Voice Listener – Mycelial Network (using Porcupine)
Wake word: "computer" (built-in) or custom "anansi"
Sends transcripts to Anansi (port 8081)
"""

import os
import time
import json
import requests
import subprocess
import numpy as np
import pvporcupine
from faster_whisper import WhisperModel

ANANSI_URL = "http://localhost:8081/execute"

# --- CONFIGURATION ---
# Replace with your Picovoice AccessKey (free from console.picovoice.ai)
ACCESS_KEY = "YOUR_PICOVOICE_ACCESS_KEY"

# Use built-in wake word "computer" (or change to "porcupine", "hey google", etc.)
# For custom "anansi", you can create a custom keyword file in the Picovoice console.
WAKE_WORD = "computer"

print("🔊 Initializing Porcupine...")
porcupine = pvporcupine.create(
    access_key=ACCESS_KEY,
    keywords=[WAKE_WORD]
)

# Speech-to-text
print("🎤 Loading Whisper model...")
stt_model = WhisperModel("tiny.en", device="cpu", compute_type="int8")

SAMPLE_RATE = 16000
AUDIO_LENGTH = 512  # Porcupine expects this frame length

def record_audio(duration=5):
    cmd = ["arecord", "-d", str(duration), "-r", str(SAMPLE_RATE), "-f", "S16_LE", "-c", "1", "-t", "raw"]
    result = subprocess.run(cmd, capture_output=True)
    return result.stdout

def transcribe_audio(audio_data):
    segments, _ = stt_model.transcribe(audio_data)
    return " ".join([seg.text for seg in segments])

def send_to_anansi(text):
    if not text.strip():
        return
    payload = {"task": "process", "args": [text], "sender": "voice"}
    try:
        r = requests.post(ANANSI_URL, json=payload, timeout=10)
        if r.status_code == 200:
            result = r.json().get("result", "No response from Anansi.")
            print(f"🤖 Response: {result}")
        else:
            print(f"❌ Anansi error: {r.status_code}")
    except Exception as e:
        print(f"❌ Failed to send: {e}")

print(f"🎤 Listening for '{WAKE_WORD}' (say it then your command)...")

while True:
    # Capture audio chunk
    cmd = ["arecord", "-d", "0.5", "-r", str(SAMPLE_RATE), "-f", "S16_LE", "-c", "1", "-t", "raw"]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        print("❌ Microphone error. Check connection.")
        time.sleep(1)
        continue

    pcm = np.frombuffer(result.stdout, dtype=np.int16)
    if len(pcm) < 512:
        continue

    # Process the entire chunk (split into 512-sample frames)
    for i in range(0, len(pcm) - 512, 512):
        frame = pcm[i:i+512]
        keyword_index = porcupine.process(frame)
        if keyword_index >= 0:
            print("🔊 Wake word detected! Recording...")
            audio_data = record_audio(5)
            print("📝 Transcribing...")
            text = transcribe_audio(audio_data)
            print(f"🗣️ You said: {text}")
            send_to_anansi(text)
            break
