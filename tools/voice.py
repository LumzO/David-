"""
David's voice — Edge TTS neural speech, no API key needed.
Voice follows the active language register, with manual override.
"""
import asyncio
import os
import re
import tempfile
import threading
import uuid
import edge_tts

VOICES = {
    "proper": "en-GB-RyanNeural",
    "slang":  "en-GB-ThomasNeural",
    "pidgin": "en-NG-AbeoNeural",
}

# Session state
STATE = {
    "enabled": False,
    "register": "proper",
    "override": "",      # if set, this voice is used regardless of register
}


def _clean_for_speech(text: str) -> str:
    """Strip markdown, emoji and chart clutter so the speech sounds natural."""
    t = text
    t = re.sub(r"```.*?```", " ", t, flags=re.S)          # code blocks
    t = re.sub(r"\[END_SESSION\]", "", t)
    t = re.sub(r"[*_#`>|]", "", t)                        # markdown marks
    t = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", t)             # links
    t = re.sub(r"^\s*[-•]\s*", "", t, flags=re.M)         # bullets
    t = re.sub(r"[^\x00-\x7F\u00C0-\u024F]", " ", t)      # emoji/symbols
    t = re.sub(r"\s+", " ", t).strip()
    return t



def _speak_blocking(text: str, voice: str):
    async def run():
        tmp = os.path.join(tempfile.gettempdir(), f"david_voice_{uuid.uuid4().hex[:8]}.mp3")
        await edge_tts.Communicate(text, voice).save(tmp)
        return tmp
    try:
        path = asyncio.run(run())
        # Simple, reliable Windows playback via MediaPlayer COM
        import subprocess
        ps = (
            "Add-Type -AssemblyName presentationCore; "
            "$p = New-Object System.Windows.Media.MediaPlayer; "
            f"$p.Open([System.Uri]'{path}'); "
            "$p.Play(); "
            "Start-Sleep -Milliseconds 500; "
            "while ($p.NaturalDuration.HasTimeSpan -eq $false) { Start-Sleep -Milliseconds 100 }; "
            "Start-Sleep -Seconds $p.NaturalDuration.TimeSpan.TotalSeconds; "
            "$p.Stop(); $p.Close()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       creationflags=0x08000000)
        try:
            os.remove(path)
        except Exception:
            pass
    except Exception as e:
        print(f"  [voice error: {e}]")

def speak(text: str, register: str = ""):
    """Speak text aloud in the current (or given) register. Non-blocking."""
    if not STATE["enabled"]:
        return
    clean = _clean_for_speech(text)
    if not clean:
        return
    if len(clean) > 900:
        clean = clean[:900].rsplit(".", 1)[0] + "."
    voice = STATE["override"] or VOICES.get(register or STATE["register"], VOICES["proper"])
    threading.Thread(target=_speak_blocking, args=(clean, voice), daemon=True).start()

def voice_control(action: str = "status", register: str = "", voice: str = "") -> str:
    """Control David's voice.
    action: 'on' | 'off' | 'status' | 'set_register' | 'set_voice' | 'clear_voice'
    register: proper | slang | pidgin
    voice: an Edge TTS voice name to force, e.g. 'en-NG-AbeoNeural'
    """
    a = action.lower().strip()

    if a == "on":
        STATE["enabled"] = True
        return f"Voice ON — speaking as {STATE['override'] or VOICES[STATE['register']]}."
    if a == "off":
        STATE["enabled"] = False
        return "Voice OFF."
    if a == "set_register":
        r = register.lower().strip()
        if r not in VOICES:
            return f"Unknown register '{register}'. Use: proper, slang, pidgin."
        STATE["register"] = r
        return f"Register set to {r} — voice {VOICES[r]}."
    if a == "set_voice":
        if not voice:
            return "Give a voice name, e.g. 'en-GB-RyanNeural'."
        STATE["override"] = voice.strip()
        return f"Voice override set to {voice.strip()}."
    if a == "clear_voice":
        STATE["override"] = ""
        return f"Voice override cleared — following register ({STATE['register']})."

    return (f"Voice is {'ON' if STATE['enabled'] else 'OFF'} | "
            f"register: {STATE['register']} | "
            f"voice: {STATE['override'] or VOICES[STATE['register']]}")