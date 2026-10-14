"""
Push-to-talk voice input for David.
Records from the mic until you press Enter, then transcribes locally with Whisper.
"""
import os
import queue
import tempfile
import threading

import numpy as np
import sounddevice as sd
from scipy.io.wavfile import write as write_wav

SAMPLE_RATE = 16000
_model = None


def _get_model():
    """Load Whisper once and keep it in memory."""
    global _model
    if _model is None:
        import whisper
        print("  [loading speech model — first time takes a moment...]")
        _model = whisper.load_model("small")   # 'small' = more accurate, slower
    return _model


def listen(max_seconds: int = 60) -> str:
    """Record from the microphone until Enter is pressed, then transcribe.
    Returns the transcribed text."""
    q = queue.Queue()
    stop_flag = threading.Event()

    def callback(indata, frames, time_info, status):
        if status:
            pass
        q.put(indata.copy())

    def wait_for_enter():
        input()
        stop_flag.set()

    print("\n  🎤 Recording — speak now, press ENTER when done...")
    threading.Thread(target=wait_for_enter, daemon=True).start()

    frames = []
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                            dtype="float32", callback=callback):
            recorded = 0
            while not stop_flag.is_set() and recorded < max_seconds * SAMPLE_RATE:
                try:
                    chunk = q.get(timeout=0.2)
                    frames.append(chunk)
                    recorded += len(chunk)
                except queue.Empty:
                    continue
    except Exception as e:
        return f"[mic error: {e}]"

    if not frames:
        return ""

    audio = np.concatenate(frames, axis=0).flatten()
    if len(audio) < SAMPLE_RATE * 0.4:
        return ""   # too short, likely an accidental press

    print("  [transcribing...]")
    tmp = os.path.join(tempfile.gettempdir(), "david_listen.wav")
    write_wav(tmp, SAMPLE_RATE, (audio * 32767).astype(np.int16))

    try:
        result = _get_model().transcribe(audio.astype(np.float32), fp16=False)
        text = (result.get("text") or "").strip()
        return text
    except Exception as e:
        return f"[transcription error: {e}]"


def listen_seconds(seconds: int = 8) -> str:
    """Record for a fixed number of seconds, then transcribe (for the web UI)."""
    print(f"\n  🎤 Recording {seconds}s...")
    audio = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                   channels=1, dtype="float32")
    sd.wait()
    audio = audio.flatten()
    if len(audio) < SAMPLE_RATE * 0.4:
        return ""
    print("  [transcribing...]")
    try:
        return (_get_model().transcribe(audio.astype(np.float32), fp16=False)
                .get("text") or "").strip()
    except Exception as e:
        return f"[transcription error: {e}]"

        # --- click-to-start / click-to-stop recording (for the web UI) ---
_REC = {"frames": [], "stream": None, "active": False}


def start_recording() -> str:
    """Begin recording from the mic. Keeps going until stop_recording()."""
    if _REC["active"]:
        return "Already recording."
    _REC["frames"] = []

    def cb(indata, frames, time_info, status):
        _REC["frames"].append(indata.copy())

    try:
        _REC["stream"] = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                        dtype="float32", callback=cb)
        _REC["stream"].start()
        _REC["active"] = True
        return "Recording started."
    except Exception as e:
        _REC["active"] = False
        return f"[mic error: {e}]"


def stop_recording() -> str:
    """Stop recording and transcribe what was captured."""
    if not _REC["active"]:
        return ""
    try:
        _REC["stream"].stop()
        _REC["stream"].close()
    except Exception:
        pass
    _REC["active"] = False

    if not _REC["frames"]:
        return ""
    audio = np.concatenate(_REC["frames"], axis=0).flatten()
    _REC["frames"] = []
    if len(audio) < SAMPLE_RATE * 0.4:
        return ""
    try:
        return (_get_model().transcribe(audio.astype(np.float32), fp16=False)
                .get("text") or "").strip()
    except Exception as e:
        return f"[transcription error: {e}]"