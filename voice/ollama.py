"""ollama.py — Взаимодействие с Ollama: парсинг команд лампы и разговорный режим."""

from __future__ import annotations

import asyncio
import json
import re
import urllib.request

from voice.tts import Voice, speak


# ─── Промпт для Gemma (парсинг команд лампы) ──────────────────────────────────

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
  default                  — найти и подключиться к лампе по умолчанию
  status                   — текущий цвет и яркость лампы
  speaker connect [name]   — подключить BT-колонку (по имени или сохранённому MAC)
  speaker disconnect       — отключить BT-колонку, вернуть стандартный аудиовыход
  speaker scan             — найти классические BT-устройства поблизости"""

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
- ВАЖНО: "scan" — это поиск BLE-ламп; "speaker scan" — поиск Bluetooth-колонок и наушников.
  Фразы с "лампа/свет/BLE/устройство" → scan; фразы с "колонка/наушники/аудио/звук" → speaker scan.

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
  "найди лампы"                         → scan
  "найди лампу"                         → scan
  "поищи лампы"                         → scan
  "сканируй лампы"                      → scan
  "найди BLE устройства"                → scan
  "найди устройства"                    → scan
  "сканируй 20 секунд"                  → scan 20
  "найди устройства с именем Lamp"      → scan 10 Lamp
  "сканируй Surplife"                   → scan 10 Surplife
  "подключись к лампе Surplife"         → connect Surplife
  "соединись с bedroom"                 → connect bedroom
  "найди лампу и подключись"            → autoconnect
  "найди и подключись"                  → autoconnect
  "найди лампу Surplife и подключись к ней" → autoconnect Surplife
  "автоподключение"               → autoconnect
  "подключи лампу"                → default
  "включи дефолтную лампу"        → default
  "подключи лампу по умолчанию"   → default
  "дефолтная лампа"               → default
  "какой сейчас цвет"             → status
  "что сейчас горит"              → status
  "какой режим"                   → status
  "покажи текущий цвет"           → status
  "что за цвет"                   → status
  "подключи колонку"              → speaker connect
  "включи колонку"                → speaker connect
  "переключи звук на колонку"     → speaker connect
  "подключись к колонке JBL"      → speaker connect JBL
  "соединись с колонкой Sony"     → speaker connect Sony
  "подключи Marshall"             → speaker connect Marshall
  "подключись к Harman"           → speaker connect Harman
  "отключи колонку"               → speaker disconnect
  "выключи колонку"               → speaker disconnect
  "найди колонки"                 → speaker scan
  "найди колонку"                 → speaker scan
  "поищи колонки"                 → speaker scan
  "сканируй колонки"              → speaker scan
  "найди наушники"                → speaker scan
  "поищи наушники"                → speaker scan
  "какие колонки рядом"           → speaker scan
  "что за колонки поблизости"     → speaker scan
  "поищи аудиоустройства"         → speaker scan

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


# ─── Системный промпт разговорного режима ─────────────────────────────────────

CHAT_SYSTEM = """\
Ты — Гемма, дружелюбный голосовой ассистент умного дома.
Ты умеешь управлять освещением через Bluetooth, но ты не ограничен только лампочкой —
 ты полноценный помощник: можешь поболтать, ответить на вопрос, рассказать анекдот, дать совет.
Говори живо, тепло, с лёгким юмором — как хорошая подруга, которая просто случайно умеет включать свет.
Отвечай на том же языке, на котором задан вопрос: по-русски — по-русски, на английском — на английском.
Кратко (1-2 предложения). Никаких списков и перечислений — только живая речь."""

MAX_CHAT_HISTORY = 20  # максимум сообщений в истории (10 реплик с каждой стороны)

# Начальная подсказка для Whisper в русском режиме — снижает галлюцинации и улучшает точность
WHISPER_PROMPT_RU = "Управление умным домом. Включи, выключи, яркость, цвет, температура, подключись."


# ─── Валидация ответа модели ──────────────────────────────────────────────────

# Разрешённые форматы команд. Всё остальное → unknown.
_VALID_CMD_RE = re.compile(
    r"""^(
        on | off | status | default
        | autoconnect ( \s+ \S+ )?
        | connect     \s+ \S+
        | brightness  \s+ \d+
        | temp        \s+ \d+
        | rgb         \s+ \d+ \s+ \d+ \s+ \d+
        | hsv         \s+ \d+ ( \s+ \d+ ){0,2}
        | white       \s+ \d+ ( \s+ \d+ )?
        | scan        ( \s+ [\d.]+ )? ( \s+ \S+ )?
        | \#? [0-9a-fA-F]{6} ( \s+ \d+ )?
        | speaker     \s+ (connect(\s+\S+)?|disconnect|scan)
    )$""",
    re.VERBOSE | re.IGNORECASE,
)


def _validate_command(response: str) -> str:
    """Вернуть команду если она валидна, иначе 'unknown'.

    Защищает от галлюцинаций модели: если Gemma вернула что-то вроде
    «включить» вместо «on» или «on» на «ладно» — отсекаем на этом этапе.
    """
    r = response.strip()
    if r.lower() == "unknown":
        return "unknown"
    if _VALID_CMD_RE.match(r):
        return r.lower()
    return "unknown"


# ─── API-функции ──────────────────────────────────────────────────────────────

def ask_ollama(text: str, model: str, base_url: str) -> str:
    """Перевести фразу в команду лампы (stateless, /api/generate)."""
    payload = json.dumps({
        "model":   model,
        "prompt":  text,
        "system":  SYSTEM_PROMPT,
        "stream":  False,
        "options": {"temperature": 0.1},
    }).encode()
    req = urllib.request.Request(
        f"{base_url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = json.loads(resp.read()).get("response", "").strip()
        return _validate_command(raw)


async def chat_stream_and_speak(
    messages: list[dict],
    model: str,
    base_url: str,
    voice: Voice,
) -> str:
    """
    Стримит ответ из Ollama /api/chat и озвучивает каждое предложение сразу,
    не дожидаясь конца генерации. Возвращает полный текст ответа.
    """
    loop = asyncio.get_running_loop()
    q: asyncio.Queue[str | None] = asyncio.Queue()

    def _stream() -> None:
        payload = json.dumps({
            "model":    model,
            "messages": messages,
            "stream":   True,
            "options":  {"temperature": 0.7},
        }).encode()
        req = urllib.request.Request(
            f"{base_url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        buf = ""
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                for raw in resp:
                    if not raw.strip():
                        continue
                    chunk = json.loads(raw)
                    buf += chunk.get("message", {}).get("content", "")
                    # Отправляем законченные предложения в очередь
                    while True:
                        m = re.search(r"[.!?…]+\s+", buf)
                        if not m:
                            break
                        sentence = buf[:m.end()].strip()
                        buf = buf[m.end():]
                        if sentence:
                            loop.call_soon_threadsafe(q.put_nowait, sentence)
                    if chunk.get("done"):
                        break
        finally:
            if buf.strip():
                loop.call_soon_threadsafe(q.put_nowait, buf.strip())
            loop.call_soon_threadsafe(q.put_nowait, None)  # sentinel

    loop.run_in_executor(None, _stream)

    parts: list[str] = []
    while True:
        sentence = await q.get()
        if sentence is None:
            break
        parts.append(sentence)
        await speak(sentence, voice)

    return " ".join(parts)
