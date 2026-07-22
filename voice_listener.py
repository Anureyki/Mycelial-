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
import requests
import pyaudio
import wave
import threading
from openwakeword import WakeWordDetection
from faster_whisper import WhisperModel

# Configuration
ANANSI_URL = "http://localhost:8081/execute"
WAKE_WORD = "hermes"   # or "computer", "boss", etc.
MODEL_PATH = "/home/anureyki/.cache/openwakeword/"

# Initialize wake word detector
print("🔊 Loading wake word model...")
wwd = WakeWordDetection(
    wakeword=WAKE_WORD,
    model_path=MODEL_PATH,
    chunk_size=16000,      # samples per inference
    threshold=0.5
)

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
    # Convert bytes to numpy array and detect wake word
    audio_data = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
    prediction = wwd.predict(audio_data)
    if prediction > 0.5:
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
