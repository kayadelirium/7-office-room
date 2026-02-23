"""audio.py — Запись голоса с микрофона (VAD по RMS-энергии)."""

from __future__ import annotations

from typing import Optional

import numpy as np
import sounddevice as sd


# ─── Настройки записи ─────────────────────────────────────────────────────────

SAMPLE_RATE       = 16_000   # Hz — Whisper требует 16 кГц
BLOCK_DURATION    = 0.2      # секунд на один блок аудио
SILENCE_THRESHOLD = 0.012    # RMS ниже этого → тишина
SILENCE_BLOCKS    = 6        # ~1.2 с тишины → конец фразы
MIN_SPEECH_BLOCKS = 2        # ~0.4 с минимальной речи
MAX_PHRASE_SEC    = 15       # максимальная длина фразы


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
