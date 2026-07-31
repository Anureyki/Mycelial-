#!/usr/bin/env python3
"""
Voice Listener for Mycelial Network
- OpenWakeWord for wake word ("Hermes")
- faster-whisper for STT
- Sends JSON to Anansi (port 8081)
- Optional Piper TTS for response
"""

import os
import sys
import time
import json
import numpy as np
import requests
import pyaudio
import wave
import threading
from openwakeword.model import Model
from faster_whisper import WhisperModel

# Configuration
ANANSI_URL = "http://localhost:8081/execute"
WAKE_WORD = "hermes"   # or "computer", "boss", etc.
MODEL_PATH = "/home/anureyki/.cache/openwakeword/"
THRESHOLD = 0.5

# Initialize wake word detector
# TODO: hermes.tflite is a placeholder (0 bytes) - no trained model yet, so wake-word
# detection is stubbed out until a real model is trained/downloaded.
print("🔊 Loading wake word model...")
try:
    wwd = Model(wakeword_model_paths=[os.path.join(MODEL_PATH, f"{WAKE_WORD}.tflite")])
except Exception as e:
    print(f"⚠️  Wake word model unavailable ({e}); wake-word detection disabled.")
    wwd = None

# Initialize STT
print("🎤 Loading Whisper model (tiny.en)...")
stt_model = WhisperModel("tiny.en", device="cpu", compute_type="int8")

# PyAudio settings
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 1024

def send_to_anansi(text):
    """Send transcribed text to Anansi."""
    payload = {
        "task": "process",
        "args": [text],
        "sender": "voice"
    }
    try:
        r = requests.post(ANANSI_URL, json=payload, timeout=10)
        if r.status_code == 200:
            result = r.json().get("result", "No response from Anansi.")
            print(f"🤖 Response: {result}")
            # Optional TTS (Piper)
            # speak_result(result)
        else:
            print(f"❌ Anansi error: {r.status_code}")
    except Exception as e:
        print(f"❌ Failed to send to Anansi: {e}")

def audio_callback(in_data, frame_count, time_info, status):
    """Process audio chunks for wake word detection."""
    if wwd is None:
        return (None, pyaudio.paContinue)
    # Convert bytes to numpy array and detect wake word
    audio_data = np.frombuffer(in_data, dtype=np.int16)
    prediction = wwd.predict(audio_data)
    if max(prediction.values()) > THRESHOLD:
        print("🔊 Wake word detected! Listening...")
        # Here we would capture a longer audio clip and transcribe
        # For this demo, we'll capture 5 seconds of audio after wake word
        # (you can implement a more sophisticated VAD + recording)
        # For now, we'll simulate by asking for input from the terminal
        # (in production, use pyaudio to record and transcribe)
        print("🎤 Please speak your command...")
        # For demo, we'll just read from stdin
        text = input(">> ")
        if text.strip():
            send_to_anansi(text)
    return (None, pyaudio.paContinue)

def main():
    print("🎤 Voice Listener started. Say 'Hermes' to wake.")
    p = pyaudio.PyAudio()
    stream = p.open(format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    frames_per_buffer=CHUNK,
                    stream_callback=audio_callback)
    stream.start_stream()
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("👋 Stopping voice listener.")
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

if __name__ == "__main__":
    main()
