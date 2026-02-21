"""
voice_assistant.py — голосовое управление BLE-лампой.

Стек:
  faster-whisper  — распознавание речи (локально, офлайн)
  Ollama / Gemma  — NLU: перевод фразы в команду лампы
  bleak           — BLE-соединение (через существующий lamp client)

Установка зависимостей:
    pip install -r requirements.txt
    ollama pull gemma3:4b   # или gemma3:1b для скорости

Запуск:
    python voice_assistant.py <UUID>
    python voice_assistant.py <UUID> --preset magic_home
    python voice_assistant.py <UUID> --model gemma3:1b --whisper tiny
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
import urllib.request
from typing import Optional

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

from lights import AbstractLampClient, SurplifeLampClient, make_client, PRESETS, find_lamp
from scanner import scan as ble_scan


# ─── Настройки по умолчанию ───────────────────────────────────────────────────

DEFAULT_LAMP_NAME = "IOTBT5AB"   # имя лампы для автоподключения при старте


# ─── TTS ──────────────────────────────────────────────────────────────────────

class SileroTTS:
    """
    Локальный TTS на основе Silero v5 (русский, офлайн).

    При первом создании загружает модель через torch.hub (~50 МБ, кешируется).
    Голоса: xenia (женский), aidar, baya, kseniya, eugene.
    """
    SAMPLE_RATE = 24_000

    # Фразы, которые синтезируются при старте и кешируются для мгновенного воспроизведения
    _CACHED_PHRASES = (
        "Включено.", "Выключено.", "Цвет установлен.",
        "Подключаюсь.", "Неизвестная команда.", "Ошибка параметров.",
        "Произошла ошибка.", "Лампа не подключена. Скажите подключись к, и назовите имя устройства.",
    )

    def __init__(self, speaker: str = "xenia") -> None:
        try:
            import torch
            self._torch = torch
        except ImportError:
            raise ImportError("Silero TTS требует torch: pip install torch omegaconf")
        print(f"Загрузка Silero TTS (speaker={speaker})...", end=" ", flush=True)
        self._model, _ = torch.hub.load(
            repo_or_dir="snakers4/silero-models",
            model="silero_tts",
            language="ru",
            speaker="v5_ru",
            verbose=False,
            trust_repo=True,
        )
        self.speaker = speaker
        print("OK")
        # Прогрев модели + кеш частых фраз (устраняет задержку первого синтеза)
        print("Прогрев TTS...", end=" ", flush=True)
        self._cache: dict[str, np.ndarray] = {}
        for phrase in self._CACHED_PHRASES:
            self._cache[phrase] = self._synthesize(phrase)
        print("OK")

    def _synthesize(self, text: str) -> "np.ndarray":
        audio = self._model.apply_tts(
            text=text,
            speaker=self.speaker,
            sample_rate=self.SAMPLE_RATE,
        )
        return audio.numpy()

    async def speak(self, text: str) -> None:
        loop = asyncio.get_running_loop()
        if text in self._cache:
            audio = self._cache[text]
        else:
            audio = await loop.run_in_executor(None, self._synthesize, text)
            self._cache[text] = audio  # кешируем для повторного использования
        def _play() -> None:
            sd.play(audio, self.SAMPLE_RATE)
            sd.wait()
        await loop.run_in_executor(None, _play)


async def speak(text: str, voice: "str | SileroTTS | None") -> None:
    """
    Произнести текст через TTS.
      voice=None       — TTS отключён
      voice=SileroTTS  — локальный Silero
      voice=str        — macOS say -v <voice>
    """
    if voice is None:
        return
    if isinstance(voice, SileroTTS):
        await voice.speak(text)
        return
    if platform.system() == "Darwin":
        proc = await asyncio.create_subprocess_exec(
            "say", "-v", voice, text,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()


async def _do_and_speak(coro, text: str, voice: "str | SileroTTS | None") -> None:
    """
    Выполняет корутину (команда лампы) и воспроизводит TTS.
    Для Silero: синтез запускается параллельно с BLE-командой,
    что экономит ~0.3–1 с на некешированных фразах.
    """
    if isinstance(voice, SileroTTS):
        loop = asyncio.get_running_loop()
        if text in voice._cache:
            audio = voice._cache[text]
            await coro
        else:
            # Синтез и BLE-команда идут параллельно
            synth = loop.run_in_executor(None, voice._synthesize, text)
            await coro
            audio = await synth
            voice._cache[text] = audio  # кешируем для следующего раза
        def _play() -> None:
            sd.play(audio, voice.SAMPLE_RATE)
            sd.wait()
        await loop.run_in_executor(None, _play)
    else:
        await coro
        await speak(text, voice)


# ─── Настройки записи ─────────────────────────────────────────────────────────

SAMPLE_RATE      = 16_000   # Hz — Whisper требует 16 кГц
BLOCK_DURATION   = 0.2      # секунд на один блок аудио
SILENCE_THRESHOLD = 0.012   # RMS ниже этого → тишина
SILENCE_BLOCKS   = 6        # ~1.2 с тишины → конец фразы
MIN_SPEECH_BLOCKS = 2       # ~0.4 с минимальной речи
MAX_PHRASE_SEC   = 15       # максимальная длина фразы


# ─── Промпт для Gemma ─────────────────────────────────────────────────────────

_COMMANDS = """\
  on                       — включить
  off                      — выключить
  white <bright> [cct]     — белый режим (bright 0-100; cct 0=тёплый 2700K, 100=холодный 6500K)
  rgb <R> <G> <B>          — цвет RGB (0-255)
  hsv <hue> [sat] [bright] — цвет HSV (hue 0-359°, sat 0-100, bright 0-100)
  brightness <0-100>       — яркость
  temp <2700-6500>         — цветовая температура в Кельвинах
  #RRGGBB [bright]         — hex-цвет, опционально с яркостью
  scan [seconds] [name]    — найти BLE-устройства поблизости (name — фильтр по имени)
  connect <name>           — подключиться к лампе по имени (или части имени)
  autoconnect [name]       — найти первую доступную лампу и сразу подключиться
  default                  — найти и подключиться к лампе по умолчанию"""

SYSTEM_PROMPT = f"""\
Ты — умный домашний ассистент с управлением освещением.
Твоя задача прямо сейчас — определить, является ли фраза командой для лампы.
Пользователь говорит на русском или английском.

Доступные команды:
{_COMMANDS}

Правила:
- Отвечай ТОЛЬКО одной командой без пояснений и пунктуации.
- Возвращай команду ТОЛЬКО если фраза явно относится к управлению лампой или устройствами.
- Во всех остальных случаях (приветствия, вопросы, разговор, просьбы не про свет) — ответь: unknown

Примеры команд лампы:
  "включи свет"            → on
  "зажги"                  → on
  "выключи"                → off
  "потуши свет"            → off
  "сделай красный"         → rgb 255 0 0
  "синий на 70"            → hsv 240 100 70
  "тёплый мягкий свет"     → white 50 10
  "холодный яркий"         → white 90 100
  "яркость 30"             → brightness 30
  "сделай потише"          → brightness 20
  "ярче"                   → brightness 80
  "ночной режим"           → white 15 0
  "рабочий свет"           → white 80 70
  "романтическая атмосфера" → rgb 180 30 10
  "3000 кельвин"           → temp 3000
  "оранжевый 60 процентов" → rgb 255 100 0
  "найди устройства"              → scan
  "сканируй 20 секунд"            → scan 20
  "найди устройства с именем Lamp" → scan 10 Lamp
  "сканируй Surplife"             → scan 10 Surplife
  "подключись к лампе Surplife"   → connect Surplife
  "подключи лампу"                → connect Lamp
  "соединись с bedroom"           → connect bedroom
  "найди лампу и подключись"      → autoconnect
  "найди и подключись"            → autoconnect
  "найди лампу Surplife и подключись к ней" → autoconnect Surplife
  "автоподключение"               → autoconnect
  "включи дефолтную лампу"        → default
  "подключи лампу по умолчанию"   → default
  "дефолтная лампа"               → default

Примеры фраз, не являющихся командами (→ unknown):
  "привет"              → unknown
  "как дела"            → unknown
  "спасибо"             → unknown
  "что ты умеешь"       → unknown
  "который час"         → unknown
  "расскажи анекдот"    → unknown
  "что такое блютус"    → unknown
  "окей"                → unknown
  "хорошо"              → unknown
  "ладно"               → unknown
  "молодец"             → unknown"""


# ─── Ollama API ───────────────────────────────────────────────────────────────

_CHAT_SYSTEM = """\
Ты — Люмико, дружелюбный голосовой ассистент умного дома.
Ты умеешь управлять освещением через Bluetooth, но ты не ограничен только лампочкой —
ты полноценный помощник: можешь поболтать, ответить на вопрос, рассказать анекдот, дать совет.
Говори живо, тепло, с лёгким юмором — как хорошая подруга, которая просто случайно умеет включать свет.
Отвечай по-русски, кратко (1-2 предложения). Никаких списков и перечислений — только живая речь."""


def _ollama_request(prompt: str, system: str, model: str, base_url: str,
                    temperature: float = 0.1) -> str:
    """Отправить запрос в Ollama и вернуть ответ модели."""
    payload = json.dumps({
        "model":   model,
        "prompt":  prompt,
        "system":  system,
        "stream":  False,
        "options": {"temperature": temperature},
    }).encode()
    req = urllib.request.Request(
        f"{base_url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read()).get("response", "").strip()


def ask_ollama(text: str, model: str, base_url: str) -> str:
    """Перевести фразу в команду лампы."""
    return _ollama_request(text, SYSTEM_PROMPT, model, base_url, temperature=0.1)


def ask_ollama_chat(text: str, model: str, base_url: str) -> str:
    """Сгенерировать разговорный ответ на непонятную или нетипичную фразу."""
    return _ollama_request(text, _CHAT_SYSTEM, model, base_url, temperature=0.7)


# ─── Запись с микрофона ───────────────────────────────────────────────────────

class VoiceRecorder:
    """
    Слушает микрофон блоками, определяет начало и конец фразы по RMS-энергии.
    Возвращает float32-массив для Whisper (16 кГц, моно).
    """

    def __init__(self) -> None:
        self._block = int(SAMPLE_RATE * BLOCK_DURATION)

    def record_phrase(self) -> Optional[np.ndarray]:
        """
        Ждёт начала речи, накапливает блоки, останавливается после тишины.
        Возвращает None если речи не было.
        """
        print("Слушаю... ", end="", flush=True)
        chunks: list[np.ndarray] = []
        silent = 0
        speaking = False
        max_blocks = int(MAX_PHRASE_SEC / BLOCK_DURATION)

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=self._block,
        ) as stream:
            while len(chunks) < max_blocks:
                block, _ = stream.read(self._block)
                rms = float(np.sqrt(np.mean(block ** 2)))

                if rms > SILENCE_THRESHOLD:
                    if not speaking:
                        print("(записываю...)", end=" ", flush=True)
                        speaking = True
                    silent = 0
                    chunks.append(block.copy())
                elif speaking:
                    chunks.append(block.copy())
                    silent += 1
                    if silent >= SILENCE_BLOCKS:
                        break

        if not speaking or len(chunks) < MIN_SPEECH_BLOCKS:
            print()
            return None

        return np.concatenate(chunks).flatten()


# ─── Выполнение команды ───────────────────────────────────────────────────────

async def execute(
    lamp: Optional[AbstractLampClient],
    command: str,
    preset: str,
    voice: Optional[str] = None,
) -> Optional[AbstractLampClient]:
    """
    Разобрать строку-команду и выполнить её на лампе.
    Возвращает новый AbstractLampClient если была команда connect, иначе None.
    """
    parts = command.strip().split()
    if not parts:
        return None

    cmd = parts[0].lower()
    print(f"→ {command}")

    # Команды лампы недоступны без подключения
    _lamp_cmds = {"on", "off", "brightness", "temp", "rgb", "white", "hsv"}
    if cmd in _lamp_cmds or cmd.startswith("#") or (len(cmd) == 6 and all(c in "0123456789abcdef" for c in cmd)):
        if lamp is None:
            print("Лампа не подключена. Скажите 'подключись к <имя>' или запустите с UUID.")
            await speak("Лампа не подключена. Скажите подключись к, и назовите имя устройства.", voice)
            return None

    try:
        if cmd == "on":
            await _do_and_speak(lamp.turn_on(), "Включено.", voice)
            print("OK")

        elif cmd == "off":
            await _do_and_speak(lamp.turn_off(), "Выключено.", voice)
            print("OK")

        elif cmd == "brightness":
            pct = int(parts[1])
            await _do_and_speak(lamp.set_brightness(pct), f"Яркость {pct} процентов.", voice)
            print("OK")

        elif cmd == "temp":
            k = int(parts[1])
            await _do_and_speak(lamp.set_color_temperature(k), f"Температура {k} кельвин.", voice)
            print("OK")

        elif cmd == "rgb":
            await _do_and_speak(
                lamp.set_color(int(parts[1]), int(parts[2]), int(parts[3])),
                "Цвет установлен.", voice,
            )
            print("OK")

        elif cmd == "white":
            if not isinstance(lamp, SurplifeLampClient):
                print("Команда white поддерживается только для Surplife.")
                await speak("Команда белого режима поддерживается только для Surplife.", voice)
                return None
            bright = int(parts[1])
            cct = int(parts[2]) if len(parts) > 2 else 50
            await _do_and_speak(
                lamp.set_white(bright, cct),
                f"Белый режим, яркость {bright} процентов.", voice,
            )
            print("OK")

        elif cmd == "hsv":
            if not isinstance(lamp, SurplifeLampClient):
                print("Команда hsv поддерживается только для Surplife.")
                await speak("Команда HSV поддерживается только для Surplife.", voice)
                return None
            hue = int(parts[1])
            sat = int(parts[2]) if len(parts) > 2 else 100
            bright = int(parts[3]) if len(parts) > 3 else 100
            await _do_and_speak(lamp.set_color_hsv(hue, sat, bright), "Цвет установлен.", voice)
            print("OK")

        elif cmd == "connect":
            if len(parts) < 2:
                print("Укажите имя устройства: connect <name>")
                await speak("Укажите имя устройства.", voice)
                return None
            name = parts[1]
            print(f"Поиск '{name}' (10 сек)...")
            await speak(f"Ищу устройство {name}.", voice)
            address = await find_lamp(name, timeout=10.0)
            if address is None:
                print(f"Устройство '{name}' не найдено.")
                await speak(f"Устройство {name} не найдено.", voice)
                return None
            new_lamp = _make_lamp(address, preset)
            print(f"Подключение к {address}...", end=" ", flush=True)
            await speak("Подключаюсь.", voice)
            await new_lamp.connect()
            name_connected = getattr(new_lamp, "device_name", address)
            print(f"OK  ({name_connected})")
            await speak(f"Подключено к {name_connected}.", voice)
            return new_lamp

        elif cmd == "autoconnect":
            name_filter = parts[1] if len(parts) > 1 else None
            label = f"'{name_filter}'" if name_filter else "любую лампу"
            print(f"Поиск: {label} (10 сек)...")
            await speak(f"Ищу {'устройство ' + name_filter if name_filter else 'лампу'}.", voice)

            if name_filter:
                address = await find_lamp(name_filter, timeout=10.0)
            else:
                results = await ble_scan(timeout=10.0, name_filter=None)
                # Берём первое устройство с именем
                named = [(d, a) for d, a in results if d.name]
                address = named[0][0].address if named else None

            if address is None:
                print("Устройства не найдены.")
                await speak("Устройства поблизости не найдены.", voice)
                return None

            new_lamp = _make_lamp(address, preset)
            print(f"Подключение к {address}...", end=" ", flush=True)
            await speak("Подключаюсь.", voice)
            await new_lamp.connect()
            name_connected = getattr(new_lamp, "device_name", address)
            print(f"OK  ({name_connected})")
            await speak(f"Подключено к {name_connected}.", voice)
            return new_lamp

        elif cmd == "default":
            print(f"Поиск лампы по умолчанию '{DEFAULT_LAMP_NAME}'...")
            await speak(f"Ищу лампу {DEFAULT_LAMP_NAME}.", voice)
            address = await find_lamp(DEFAULT_LAMP_NAME, timeout=10.0)
            if address is None:
                print(f"Лампа '{DEFAULT_LAMP_NAME}' не найдена.")
                await speak(f"Лампа {DEFAULT_LAMP_NAME} не найдена.", voice)
                return None
            new_lamp = _make_lamp(address, preset)
            print(f"Подключение к {address}...", end=" ", flush=True)
            await speak("Подключаюсь.", voice)
            await new_lamp.connect()
            name_connected = getattr(new_lamp, "device_name", address)
            print(f"OK  ({name_connected})")
            await speak(f"Подключено к {name_connected}.", voice)
            return new_lamp

        elif cmd == "scan":
            timeout = 10.0
            name_filter = None
            if len(parts) > 1:
                try:
                    timeout = float(parts[1])
                except ValueError:
                    name_filter = parts[1]
            if len(parts) > 2:
                name_filter = parts[2]
            label = f'"{name_filter}"' if name_filter else "все устройства"
            print(f"Сканирование BLE ({timeout:.0f} сек, фильтр: {label})...")
            await speak(f"Сканирую, подождите {int(timeout)} секунд.", voice)
            results = await ble_scan(timeout=timeout, name_filter=name_filter)
            await speak(f"Найдено {len(results)} устройств.", voice)
            return None

        elif cmd.startswith("#") or (len(cmd) == 6 and all(c in "0123456789abcdef" for c in cmd)):
            from lights import parse_hex_color, rgb_to_hsv
            r, g, b = parse_hex_color(cmd)
            if isinstance(lamp, SurplifeLampClient):
                h, s, v = rgb_to_hsv(r, g, b)
                bri = int(parts[1]) if len(parts) > 1 else v
                coro = lamp.set_white(bri) if s == 0 else lamp.set_color_hsv(h, s, bri)
            else:
                coro = lamp.set_color(r, g, b)
            await _do_and_speak(coro, "Цвет установлен.", voice)
            print("OK")

        else:
            print(f"Неизвестная команда: {cmd!r}")
            await speak("Неизвестная команда.", voice)
            return None

    except (IndexError, ValueError) as e:
        print(f"Ошибка параметров: {e}")
        await speak("Ошибка параметров.", voice)
    except Exception as e:
        print(f"Ошибка: {e}")
        await speak("Произошла ошибка.", voice)

    return None


# ─── Главный цикл ─────────────────────────────────────────────────────────────

async def voice_loop(
    lamp: Optional[AbstractLampClient],
    preset: str,
    whisper: WhisperModel,
    ollama_model: str,
    ollama_url: str,
    voice: Optional[str] = None,
) -> None:
    if lamp is not None:
        name = getattr(lamp, "device_name", lamp.address)
        print(f"\nГолосовое управление: {name}  (пресет: {preset})")
    else:
        print("\nГолосовое управление (без лампы — только сканирование)")
    print("Говорите команды. Ctrl+C для выхода.\n")

    recorder = VoiceRecorder()

    while True:
        try:
            audio = recorder.record_phrase()
            if audio is None:
                continue

            # Распознавание речи
            segments, _ = whisper.transcribe(
                audio,
                language="ru",
                beam_size=1,
                vad_filter=False,  # свой VAD уже есть в VoiceRecorder
            )
            text = " ".join(s.text for s in segments).strip()
            if not text:
                print("(пусто)")
                continue

            print(f'"{text}"')

            # Парсинг через Gemma → команда лампы
            try:
                command = ask_ollama(text, ollama_model, ollama_url)
            except OSError as e:
                print(f"Ollama недоступна ({e}). Запустите: ollama serve")
                continue
            except Exception as e:
                print(f"Ошибка Ollama: {e}")
                continue

            # Если команда непонятна — Gemma отвечает в разговорном режиме
            if command.lower() == "unknown":
                try:
                    reply = ask_ollama_chat(text, ollama_model, ollama_url)
                except Exception:
                    reply = "Не понял команду."
                print(f"Gemma: {reply}")
                await speak(reply, voice)
                print()
                continue

            # Выполнение
            new_lamp = await execute(lamp, command, preset, voice)
            if new_lamp is not None:
                if lamp is not None:
                    await lamp.disconnect()
                lamp = new_lamp
                name = getattr(lamp, "device_name", lamp.address)
                print(f"Активная лампа: {name}")
            print()

        except KeyboardInterrupt:
            print("\nВыход.")
            break


# ─── Подключение к лампе ──────────────────────────────────────────────────────

def _make_lamp(address: str, preset: str) -> AbstractLampClient:
    if preset == "surplife":
        return SurplifeLampClient(address)
    return make_client(address, preset)


# ─── Точка входа ──────────────────────────────────────────────────────────────

async def async_main(args: argparse.Namespace) -> None:
    print(f"Загрузка Whisper ({args.whisper})...", end=" ", flush=True)
    whisper = WhisperModel(args.whisper, device="cpu", compute_type="int8")
    print("OK")

    if args.no_tts:
        voice: str | SileroTTS | None = None
    elif args.tts_backend == "silero":
        voice = SileroTTS(args.silero_speaker)
    else:
        voice = args.voice  # macOS say

    if args.address is None:
        await voice_loop(None, args.preset, whisper, args.model, args.ollama, voice)
        return

    lamp = _make_lamp(args.address, args.preset)

    if isinstance(lamp, SurplifeLampClient):
        print(f"Подключение к {args.address}...", end=" ", flush=True)
        await lamp.connect()
        print(f"OK  ({lamp.device_name})")
        try:
            await voice_loop(lamp, args.preset, whisper, args.model, args.ollama, voice)
        finally:
            await lamp.disconnect()
    else:
        async with lamp:
            await voice_loop(lamp, args.preset, whisper, args.model, args.ollama, voice)


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
                        choices=["tiny", "base", "small", "medium"],
                        help="Модель Whisper (по умолчанию: small)")
    parser.add_argument("--ollama", default="http://localhost:11434",
                        help="URL Ollama (по умолчанию: http://localhost:11434)")
    parser.add_argument("--tts-backend", choices=["say", "silero"], default="say",
                        help="TTS-движок: say (macOS) или silero (локальный, офлайн)")
    parser.add_argument("--voice", "-V", default="Milena",
                        help="Голос для macOS say (только с --tts-backend say, по умолчанию: Milena)")
    parser.add_argument("--silero-speaker", default="kseniya",
                        choices=["xenia", "aidar", "baya", "kseniya", "eugene"],
                        help="Голос Silero (только с --tts-backend silero, по умолчанию: xenia)")
    parser.add_argument("--no-tts", action="store_true",
                        help="Отключить голосовые ответы")

    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
