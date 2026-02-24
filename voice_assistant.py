"""
voice_assistant.py — голосовой ассистент для управления BLE-лампой и BT-колонкой.

Пайплайн:
  микрофон  →  faster-whisper (STT)  →  Ollama/Gemma (NLU)  →  BLE-лампа / BT-колонка
                                                              →  Silero TTS (голосовой ответ)

Установка:
    pip install -r requirements.txt
    ollama pull gemma3:4b          # или gemma3:1b для скорости
    # Linux: sudo apt install libportaudio2

Запуск:
    python voice_assistant.py                        # без лампы — сканирование и подключение
    python voice_assistant.py <UUID>                 # сразу подключиться к лампе
    python voice_assistant.py --stt-lang ru          # явный русский (меньше галлюцинаций)
    python voice_assistant.py --model gemma3:1b --whisper tiny  # быстрый режим
    python voice_assistant.py --no-tts               # без голосовых ответов
    python voice_assistant.py --speaker-mac AA:BB:CC:DD:EE:FF --speaker-name JBL
"""

from __future__ import annotations

import argparse
import asyncio
import urllib.request

from faster_whisper import WhisperModel

from voice.audio import VoiceRecorder
from voice.commands import execute, make_lamp
from voice.ollama import (
    ask_ollama, chat_stream_and_speak,
    CHAT_SYSTEM, MAX_CHAT_HISTORY, WHISPER_PROMPT_RU,
)
from voice.tts import SileroTTS, Voice, _detect_lang, speak  # Voice = SileroTTS | None
import speaker
from lights import AbstractLampClient, SurplifeLampClient, PRESETS


# ─── Главный цикл ─────────────────────────────────────────────────────────────

async def voice_loop(
    lamps: list[AbstractLampClient],
    preset: str,
    whisper: WhisperModel,
    ollama_model: str,
    ollama_url: str,
    voice: Voice = None,
    stt_lang: str = "",
) -> None:
    if lamps:
        names = ", ".join(getattr(l, "device_name", l.address) for l in lamps)
        print(f"\nГолосовое управление: {names}  (пресет: {preset})")
    else:
        print("\nГолосовое управление (без лампы — только сканирование)")
    print("Говорите команды. Ctrl+C для выхода.\n")

    recorder = VoiceRecorder()
    loop = asyncio.get_running_loop()
    chat_history: list[dict] = []

    while True:
        try:
            audio = recorder.record_phrase()
            if audio is None:
                continue

            # Распознавание речи (в executor — не блокирует цикл событий)
            def _transcribe() -> str:
                # vad_filter убирает галлюцинации, initial_prompt улучшает точность для RU
                prompt = WHISPER_PROMPT_RU if stt_lang == "ru" else None
                segs, _ = whisper.transcribe(
                    audio,
                    language=stt_lang or None,
                    beam_size=1,
                    vad_filter=True,
                    initial_prompt=prompt,
                )
                return " ".join(s.text for s in segs).strip()

            text = await loop.run_in_executor(None, _transcribe)
            if not text:
                print("(пусто)")
                continue

            print(f'"{text}"')

            # Парсинг через Gemma → команда лампы (stateless)
            try:
                command = await loop.run_in_executor(None, ask_ollama, text, ollama_model, ollama_url)
            except OSError as e:
                print(f"Ollama недоступна ({e}). Запустите: ollama serve")
                continue
            except Exception as e:
                print(f"Ошибка Ollama: {e}")
                continue

            # Если команда непонятна — Гемма отвечает с контекстом разговора
            if command.lower() == "unknown":
                chat_history.append({"role": "user", "content": text})
                lang = _detect_lang(text)
                if lang == "en":
                    system = CHAT_SYSTEM + "\nIMPORTANT: The user wrote in English. You MUST reply in English only."
                else:
                    system = CHAT_SYSTEM
                messages = [{"role": "system", "content": system}] + chat_history
                # Стриминг: первое предложение озвучивается до конца генерации
                reply = await chat_stream_and_speak(messages, ollama_model, ollama_url, voice)
                if reply:
                    chat_history.append({"role": "assistant", "content": reply})
                    if len(chat_history) > MAX_CHAT_HISTORY:
                        chat_history = chat_history[-MAX_CHAT_HISTORY:]
                else:
                    reply = "Что-то пошло не так, извини."
                    chat_history.pop()
                    await speak(reply, voice)
                print(f"Гемма: {reply}")
                print()
                continue

            # Выполнение команды лампы
            new_lamps = await execute(lamps, command, preset, voice)
            if new_lamps is not None:
                # Отключить лампы, которых нет в новом списке
                for old in lamps:
                    if old not in new_lamps:
                        try:
                            await old.disconnect()
                        except Exception:
                            pass
                lamps = new_lamps
                names = ", ".join(getattr(l, "device_name", l.address) for l in lamps)
                print(f"Активные лампы: {names}")
            print()

        except KeyboardInterrupt:
            print("\nВыход.")
            break


# ─── Проверка и автозапуск Ollama ─────────────────────────────────────────────

def _ollama_reachable(url: str) -> bool:
    try:
        urllib.request.urlopen(f"{url}/api/tags", timeout=3)
        return True
    except Exception:
        return False


async def _ensure_ollama(url: str) -> bool:
    """Проверить доступность Ollama; при необходимости запустить `ollama serve`.

    Возвращает True если Ollama готова к работе, False если запустить не удалось.
    """
    if _ollama_reachable(url):
        return True

    print("Ollama не отвечает. Запускаю `ollama serve`...", end=" ", flush=True)
    try:
        proc = await asyncio.create_subprocess_exec(
            "ollama", "serve",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        print("\nollama не найден. Установите: https://ollama.com")
        return False

    for _ in range(10):
        await asyncio.sleep(1)
        if _ollama_reachable(url):
            print("OK")
            return True

    proc.terminate()
    print("\nОllama не запустилась за 10 сек. Запустите вручную: ollama serve")
    return False


# ─── Точка входа ──────────────────────────────────────────────────────────────

async def async_main(args: argparse.Namespace) -> None:
    stt_lang = args.stt_lang

    if not await _ensure_ollama(args.ollama):
        return

    print(f"Загрузка Whisper ({args.whisper})...", end=" ", flush=True)
    whisper = WhisperModel(args.whisper, device="cpu", compute_type="int8")
    print("OK")

    if args.no_tts:
        voice: Voice = None
    else:
        voice = SileroTTS(args.silero_speaker, args.silero_en_speaker)

    speaker.configure(mac=args.speaker_mac, name=args.speaker_name)

    if args.address is None:
        await voice_loop([], args.preset, whisper, args.model, args.ollama, voice, stt_lang)
        return

    lamp = make_lamp(args.address, args.preset)

    if isinstance(lamp, SurplifeLampClient):
        print(f"Подключение к {args.address}...", end=" ", flush=True)
        await lamp.connect()
        print(f"OK  ({lamp.device_name})")
        try:
            await voice_loop([lamp], args.preset, whisper, args.model, args.ollama, voice, stt_lang)
        finally:
            await lamp.disconnect()
    else:
        async with lamp:
            await voice_loop([lamp], args.preset, whisper, args.model, args.ollama, voice, stt_lang)


def main() -> None:
    valid_presets = list(PRESETS.keys()) + ["surplife"]

    parser = argparse.ArgumentParser(
        description="Голосовое управление BLE-лампой (Whisper + Ollama/Gemma)",
    )
    parser.add_argument("address", metavar="UUID", nargs="?", default=None,
                        help="MAC/UUID лампы (необязательно; без UUID — только сканирование)")
    parser.add_argument("--preset", "-p", default="surplife",
                        choices=valid_presets,
                        help="Пресет протокола (по умолчанию: surplife)")
    parser.add_argument("--model", "-m", default="gemma3:4b",
                        help="Модель Ollama (по умолчанию: gemma3:4b)")
    parser.add_argument("--whisper", "-w", default="small",
                        choices=["tiny", "base", "small", "medium", "large-v3-turbo"],
                        help="Модель Whisper (по умолчанию: small)")
    parser.add_argument("--stt-lang", default="",
                        help="Язык распознавания: ru, en (пусто = авто-определение)")
    parser.add_argument("--ollama", default="http://127.0.0.1:11434",
                        help="URL Ollama (по умолчанию: http://127.0.0.1:11434)")
    parser.add_argument("--silero-speaker", default="kseniya",
                        choices=["xenia", "aidar", "baya", "kseniya", "eugene"],
                        help="Русский голос Silero (по умолчанию: kseniya)")
    parser.add_argument("--silero-en-speaker", default="en_0",
                        help="Английский голос Silero (по умолчанию: en_0; доступны en_0…en_117, lj_16khz)")
    parser.add_argument("--no-tts", action="store_true",
                        help="Отключить голосовые ответы")
    parser.add_argument("--speaker-mac", default="",
                        help="MAC-адрес BT-колонки (AA:BB:CC:DD:EE:FF)")
    parser.add_argument("--speaker-name", default="",
                        help="Подстрока имени колонки в списке аудиоустройств (напр. 'JBL', 'Sony')")

    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
