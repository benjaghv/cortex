"""
cortex.voice
────────────
Speech-to-text input (dictation) for the CLI — lets you *talk* to cortex
instead of typing. NOT an agent tool: this is input capture for `/voice`
(in chat) and the `cortex voice` command.

Dependencies are imported lazily so the rest of cortex works without them:
    pip install SpeechRecognition pyaudio

Transcription uses Google Web Speech API (no key, needs internet).
"""

from __future__ import annotations

from rich.text import Text

from cortex.config import Settings
from cortex.display import console


def _missing_deps_msg() -> None:
    console.print()
    console.print("  [bold #F87171]✗ Voz no disponible[/] — faltan dependencias.")
    console.print("  Instala:  [bold]pip install SpeechRecognition pyaudio[/]")
    console.print("  [dim]En Windows, si pyaudio falla:[/] [bold]pip install pipwin && pipwin install pyaudio[/]")
    console.print()


def listen(cfg: Settings, language: str | None = None,
           timeout: int = 8, phrase_time_limit: int = 15) -> "str | None":
    """Capture one phrase from the mic and return the transcript (or None).

    Shows a live spinner while adjusting/listening. All user-facing output goes
    through the shared `console`; never bare print(). Returns None on any failure
    (deps missing, no mic, timeout, unintelligible, network) — the caller decides
    what to do next.
    """
    try:
        import speech_recognition as sr
    except ImportError:
        _missing_deps_msg()
        return None

    lang = language or getattr(cfg, "voice_language", "es-ES")
    recognizer = sr.Recognizer()

    try:
        mic = sr.Microphone()
    except Exception as e:
        console.print(f"\n  [bold #F87171]✗ No se encontró micrófono[/] [dim]({e})[/]\n")
        return None

    try:
        with mic as source, console.status(
            Text("Calibrando micrófono…", style="agent.think"),
            spinner="dots", spinner_style="#7C5CFF",
        ) as status:
            recognizer.adjust_for_ambient_noise(source, duration=0.6)
            status.update(Text("🎤 Escuchando… habla ahora", style="agent.think"))
            audio = recognizer.listen(source, timeout=timeout,
                                      phrase_time_limit=phrase_time_limit)
            status.update(Text("Transcribiendo…", style="agent.think"))
            text = recognizer.recognize_google(audio, language=lang)
        return (text or "").strip() or None

    except sr.WaitTimeoutError:
        console.print("\n  [dim]⏱  No escuché nada a tiempo. Intenta de nuevo.[/]\n")
    except sr.UnknownValueError:
        console.print("\n  [dim]🔇 No entendí el audio. Intenta de nuevo.[/]\n")
    except sr.RequestError as e:
        console.print(f"\n  [bold #F87171]✗ Error del servicio de voz[/] [dim]({e})[/]\n")
    except Exception as e:
        console.print(f"\n  [bold #F87171]✗ Error inesperado de voz[/] [dim]({type(e).__name__}: {e})[/]\n")
    return None
