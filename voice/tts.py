"""tts.py — Silero TTS, тип Voice, определение языка."""

from __future__ import annotations

import asyncio
import re

import numpy as np
import sounddevice as sd


# ─── Очистка текста для Silero ────────────────────────────────────────────────

# Silero не умеет произносить emoji, markdown и прочие спецсимволы — убираем их
_STRIP_RE = re.compile(r'[^\w\s.,!?;:\-–—()\'"«»]', re.UNICODE)


def _clean_for_tts(text: str) -> str:
    """Убрать emoji и спецсимволы перед передачей в Silero."""
    text = _STRIP_RE.sub('', text)
    return re.sub(r'\s+', ' ', text).strip()


def _digits_to_words(text: str, lang: str) -> str:
    """Заменить числа словами: «яркость 70» → «яркость семьдесят»."""
    from num2words import num2words

    def _replace(m: re.Match) -> str:
        try:
            return num2words(int(m.group()), lang=lang)
        except Exception:
            return m.group()

    return re.sub(r'\d+', _replace, text)


# ─── Определение языка ────────────────────────────────────────────────────────

def _detect_lang(text: str) -> str:
    """Вернуть 'en' или 'ru' по соотношению латиницы и кириллицы в тексте."""
    lat = sum(1 for c in text if c.isascii() and c.isalpha())
    cyr = sum(1 for c in text if "\u0400" <= c <= "\u04ff")
    return "en" if lat > cyr else "ru"


# ─── Silero TTS ───────────────────────────────────────────────────────────────

class SileroTTS:
    """
    Локальный TTS на основе Silero (русский v5 + английский v3, офлайн).

    Язык ответа определяется автоматически по соотношению латиницы и кириллицы.
    Русские голоса: xenia, aidar, baya, kseniya, eugene.
    Английские голоса: en_0 … en_117, lj_16khz.
    """

    SAMPLE_RATE = 24_000

    # Фразы, которые синтезируются при старте и кешируются для мгновенного воспроизведения
    _CACHED_PHRASES = (
        "Включено.", "Выключено.", "Цвет установлен.",
        "Подключаюсь.", "Неизвестная команда.", "Ошибка параметров.",
        "Произошла ошибка.", "Лампа не подключена. Скажите подключись к, и назовите имя устройства.",
    )

    def __init__(self, speaker: str = "xenia", en_speaker: str = "en_0") -> None:
        try:
            import torch
        except ImportError:
            raise ImportError("Silero TTS требует torch: pip install torch omegaconf")
        print(f"Загрузка Silero TTS RU (speaker={speaker})...", end=" ", flush=True)
        self._model_ru, _ = torch.hub.load(
            repo_or_dir="snakers4/silero-models",
            model="silero_tts",
            language="ru",
            speaker="v5_ru",
            verbose=False,
            trust_repo=True,
        )
        self.speaker = speaker
        print("OK")
        print(f"Загрузка Silero TTS EN (speaker={en_speaker})...", end=" ", flush=True)
        self._model_en, _ = torch.hub.load(
            repo_or_dir="snakers4/silero-models",
            model="silero_tts",
            language="en",
            speaker="v3_en",
            verbose=False,
            trust_repo=True,
        )
        self.en_speaker = en_speaker
        print("OK")
        print("Прогрев TTS...", end=" ", flush=True)
        self._cache: dict[str, np.ndarray] = {}
        for phrase in self._CACHED_PHRASES:
            self._cache[phrase] = self._synthesize(phrase)
        print("OK")

    def _synthesize(self, text: str) -> np.ndarray:
        text = _clean_for_tts(text)
        if not text:
            return np.zeros(0, dtype=np.float32)
        lang = _detect_lang(text)
        text = _digits_to_words(text, lang)
        if lang == "en":
            audio = self._model_en.apply_tts(
                text=text,
                speaker=self.en_speaker,
                sample_rate=self.SAMPLE_RATE,
            )
        else:
            audio = self._model_ru.apply_tts(
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
            self._cache[text] = audio

        if audio.size == 0:
            return

        def _play() -> None:
            sd.play(audio, self.SAMPLE_RATE)
            sd.wait()

        await loop.run_in_executor(None, _play)


# ─── Тип голоса и вспомогательные функции ─────────────────────────────────────

# SileroTTS = локальный движок, None = TTS отключён
Voice = SileroTTS | None


async def speak(text: str, voice: Voice) -> None:
    """Произнести текст через Silero или ничего не делать если voice=None."""
    if voice is None:
        return
    await voice.speak(text)


async def do_and_speak(coro, text: str, voice: Voice) -> None:
    """Выполняет BLE-команду и воспроизводит TTS. Для Silero синтез идёт параллельно с командой."""
    if voice is None:
        await coro
        return

    loop = asyncio.get_running_loop()
    if text in voice._cache:
        audio = voice._cache[text]
        await coro
    else:
        # Синтез и BLE-команда идут параллельно
        synth = loop.run_in_executor(None, voice._synthesize, text)
        await coro
        audio = await synth
        voice._cache[text] = audio

    def _play() -> None:
        sd.play(audio, voice.SAMPLE_RATE)
        sd.wait()

    await loop.run_in_executor(None, _play)
