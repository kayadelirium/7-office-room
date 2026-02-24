"""
surplife_client.py — клиент для ламп Surplife (проприетарный BLE-протокол).

─────────────────────────────────────────────────────────
ПРОТОКОЛ SURPLIFE
─────────────────────────────────────────────────────────
Каждый пакет имеет фиксированный 8-байтный заголовок, за которым следует payload:

  Байты 0-1  [seq_hi, seq_lo]  — порядковый номер пакета (big-endian uint16).
                                  Начинается с 1 после connect(), увеличивается
                                  на 1 при каждой отправке. Лампа, по всей
                                  видимости, использует его для деduplication.

  Байты 2-3  [0x80, 0x00]     — флаги направления (host → lamp).
                                  Ответы от лампы имеют другие флаги.

  Байты 4-5  [len_hi, len_lo]  — длина payload в байтах (big-endian uint16).

  Байт  6    cmd              — код команды (см. таблицу ниже).
  Байт  7    sub              — субкоманда / уточнение.

  Байты 8+   payload          — данные команды (0 или более байт).

Известные команды:
  cmd=0x01 sub=0x0C            — инициализация соединения (handshake, payload пуст)
  cmd=0x0D sub=0x0A payload=…  — запрос информации об устройстве (версия прошивки и др.)
  cmd=0x06 sub=0x0A payload=…  — запрос текущего состояния лампы
  cmd=0x03 sub=0x0A [0x71 0x23] — включить питание
  cmd=0x03 sub=0x0A [0x71 0x24] — выключить питание
  cmd=0x0F sub=0x0A payload=14б — установить режим (цвет или белый)

─────────────────────────────────────────────────────────
РЕЖИМ ЦВЕТА (HSV)
─────────────────────────────────────────────────────────
cmd=0x0F, sub=0x0A, payload 14 байт:
  [E0 01 00 A1  h  s  b  00  00 00 00 14 00 00]

  Байт [3] = 0xA1 — маркер цветного режима (Hue/Sat/Val)
  Байт [4] = hue // 2  (Surplife принимает 0-179, поэтому 0-359° делим на 2)
  Байт [5] = sat       (насыщенность 0-100%)
  Байт [6] = bright    (яркость 0-100%)
  Остальные байты — фиксированные magic-числа.

─────────────────────────────────────────────────────────
РЕЖИМ БЕЛОГО (CCT + яркость)
─────────────────────────────────────────────────────────
cmd=0x0F, sub=0x0A, payload 14 байт:
  [E0 01 00 B1  00 00 00  cct  bright  00 00 14 00 00]

  Байт [3] = 0xB1 — маркер белого режима (Color Temp + Brightness)
  Байт [7] = cct    (цветовая температура 0=тёплый 2700K .. 100=холодный 6500K)
  Байт [8] = bright (яркость 0-100%)
  Остальные байты — фиксированные magic-числа.

─────────────────────────────────────────────────────────
GATT-ХАРАКТЕРИСТИКИ
─────────────────────────────────────────────────────────
  FF01 — write (команды host → лампа)
  FF02 — read / notify (состояние лампа → host)
  FF22 — notify (дополнительный канал, назначение не установлено)

─────────────────────────────────────────────────────────
ПРИМЕР ИСПОЛЬЗОВАНИЯ
─────────────────────────────────────────────────────────
    from lights import SurplifeLampClient

    async with SurplifeLampClient("<UUID>") as lamp:
        await lamp.turn_on()
        await lamp.set_white(80, 30)             # яркость 80%, тёплый (2700 K)
        await lamp.set_color_temperature(4000)   # 4000 K
        await lamp.set_color(255, 128, 0)        # оранжевый через RGB → HSV
        await lamp.set_color_hsv(120, 100, 80)   # зелёный через HSV напрямую
"""

from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient, BleakScanner

from .lamp_client import AbstractLampClient, rgb_to_hsv

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GATT UUID (полный формат: 0000XXXX-0000-1000-8000-00805f9b34fb)
# ---------------------------------------------------------------------------

FF01 = "0000ff01-0000-1000-8000-00805f9b34fb"  # write: команды host → лампа
FF02 = "0000ff02-0000-1000-8000-00805f9b34fb"  # read / notify: состояние лампы
FF22 = "0000ff22-0000-1000-8000-00805f9b34fb"  # notify: дополнительный канал


# ---------------------------------------------------------------------------
# Формирование пакетов (приватные функции)
# ---------------------------------------------------------------------------

def _packet(seq: int, cmd: int, sub: int, payload: bytes) -> bytes:
    """
    Собрать пакет Surplife из заголовка и payload.

    Структура заголовка (8 байт):
      [seq_hi, seq_lo]  — порядковый номер (big-endian)
      [0x80, 0x00]      — флаги: host → lamp
      [len_hi, len_lo]  — длина payload (big-endian)
      [cmd, sub]        — код команды / субкоманды

    seq инкрементируется при каждом вызове write_raw() через _next_seq().
    """
    return bytes([
        (seq >> 8) & 0xFF, seq & 0xFF,   # seq big-endian
        0x80, 0x00,                       # флаги направления
        (len(payload) >> 8) & 0xFF, len(payload) & 0xFF,  # len big-endian
        cmd, sub,                         # команда и субкоманда
    ]) + payload


# Инициализационные команды (отправляются при подключении в _handshake)

def _cmd_init(seq: int) -> bytes:
    """Handshake: инициализация соединения (cmd=0x01, sub=0x0C, payload пуст)."""
    return _packet(seq, 0x01, 0x0C, b"")


def _cmd_device_info(seq: int) -> bytes:
    """Запрос информации об устройстве (версия прошивки, модель и др.)."""
    # Payload — набор ID запрашиваемых параметров (точная семантика не установлена)
    return _packet(seq, 0x0D, 0x0A,
                   bytes([0x10, 0x14, 0x1A, 0x02, 0x15, 0x01, 0x07, 0x07,
                          0x00, 0x00, 0x00, 0x00]))


def _cmd_state_query(seq: int) -> bytes:
    """Запрос текущего состояния лампы (яркость, режим, цвет)."""
    # Payload — ID запрашиваемых полей состояния
    return _packet(seq, 0x06, 0x0A, bytes([0xEA, 0x81, 0x8A, 0x8B, 0x59]))


def _cmd_power_on(seq: int) -> bytes:
    """Включить питание."""
    return _packet(seq, 0x03, 0x0A, bytes([0x71, 0x23]))


def _cmd_power_off(seq: int) -> bytes:
    """Выключить питание."""
    return _packet(seq, 0x03, 0x0A, bytes([0x71, 0x24]))


def _cmd_set_white(seq: int, brightness: int, color_temp: int = 50) -> bytes:
    """
    Собрать команду белого режима.

    brightness : яркость 0-100%
    color_temp : цветовая температура 0-100%
                 0  = тёплый белый (≈2700 K)
                 50 = нейтральный  (≈4600 K)
                 100 = холодный    (≈6500 K)

    Payload (14 байт):
      [E0 01 00 B1 00 00 00 cct bright 00 00 14 00 00]
      Байт [3] = 0xB1 — маркер белого режима
      Байт [7] = cct    (ограничен до 0-100)
      Байт [8] = bright (ограничен до 0-100)
    """
    b   = max(0, min(100, brightness))
    cct = max(0, min(100, color_temp))
    return _packet(seq, 0x0F, 0x0A,
                   bytes([0xE0, 0x01, 0x00, 0xB1, 0x00, 0x00, 0x00, cct,
                          b,    0x00, 0x00, 0x14, 0x00, 0x00]))


def _cmd_set_color(seq: int, hue: int,
                   saturation: int = 100, brightness: int = 100) -> bytes:
    """
    Собрать команду цветного режима (HSV).

    hue        : оттенок 0-359° (лампа принимает hue // 2, т.е. 0-179)
    saturation : насыщенность 0-100%
    brightness : яркость 0-100%

    Payload (14 байт):
      [E0 01 00 A1 h//2 sat bright 00 00 00 00 14 00 00]
      Байт [3] = 0xA1 — маркер цветного режима
      Байт [4] = hue // 2  (масштабирование: лампа ожидает 0-179)
      Байт [5] = saturation
      Байт [6] = brightness
    """
    h = (hue // 2) & 0xFF   # масштабирование: 0-359 → 0-179
    s = max(0, min(100, saturation))
    b = max(0, min(100, brightness))
    return _packet(seq, 0x0F, 0x0A,
                   bytes([0xE0, 0x01, 0x00, 0xA1, h, s, b, 0x00,
                          0x00, 0x00, 0x00, 0x14, 0x00, 0x00]))


# ---------------------------------------------------------------------------
# Клиент
# ---------------------------------------------------------------------------

class SurplifeLampClient(AbstractLampClient):
    """
    Клиент для ламп Surplife (проприетарный протокол с seq-заголовком).

    Наследует AbstractLampClient — поддерживает context manager (async with)
    и единый интерфейс с другими типами ламп из lamp_client.py.

    Особенности реализации:
      - Каждая команда получает уникальный seq (порядковый номер).
      - После каждой записи в FF01 делается пауза 0.3 с (лампа медленная).
      - При connect() выполняется трёхэтапное рукопожатие:
          1. init        (cmd=0x01)
          2. device_info (cmd=0x0D)
          3. state_query (cmd=0x06)
      - Подписка на FF22 и FF02 активируется при connect() даже без callback
        (это необходимо для стабильной работы некоторых прошивок).

    Методы, уникальные для Surplife (не в AbstractLampClient):
      set_white(brightness, color_temp)       — прямое управление CCT
      set_color_hsv(hue, sat, bright)         — HSV без RGB-конвертации
    """

    def __init__(self, address: str) -> None:
        """
        address — CoreBluetooth UUID (macOS) или MAC (Linux/Windows).
                  Получить можно через: python main.py --scan
        """
        self.address = address
        self.device_name: str = address   # обновляется до имени лампы после connect()
        self._client: BleakClient | None = None
        self._seq = 0                     # счётчик пакетов, сбрасывается при connect()
        # Последнее состояние для set_brightness (чтобы не терять текущий цвет)
        self._last_mode: str = "white"    # "white" | "hsv"
        self._last_hue: int = 0
        self._last_sat: int = 100
        self._last_cct: int = 50
        self._last_bright: int = 100

    # ── Соединение ─────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    def _next_seq(self) -> int:
        """Вернуть следующий порядковый номер пакета (инкрементирует счётчик)."""
        self._seq += 1
        return self._seq

    async def connect(self, scan_timeout: float = 15.0) -> None:
        """
        Найти лампу по адресу, подключиться и выполнить инициализационное рукопожатие.

        scan_timeout — сколько секунд ждать появления устройства в эфире.
        На macOS Bluetooth-устройство должно быть сначала обнаружено сканированием —
        прямое подключение по UUID без этого шага не работает.

        После физического подключения:
          1. Подписываемся на notify-каналы FF22 и FF02.
          2. Ждём 0.3 с (лампа инициализирует стек).
          3. Отправляем трёхэтапный handshake (init → device_info → state_query).
        """
        device = await BleakScanner.find_device_by_address(
            self.address, timeout=scan_timeout
        )
        if device is None:
            raise ConnectionError(
                f"Устройство {self.address} не найдено. "
                "Убедитесь что лампа включена и находится рядом."
            )
        self.device_name = device.name or self.address
        self._client = BleakClient(device)
        await self._client.connect()
        if not self._client.is_connected:
            raise ConnectionError(f"Не удалось подключиться к {self.address}")

        # Подписка на оба notify-канала.
        # lambda *_: None — нас не интересуют данные уведомлений,
        # но сама подписка нужна для корректной работы прошивки лампы.
        for uuid in (FF22, FF02):
            try:
                await self._client.start_notify(uuid, lambda *_: None)
            except Exception:
                pass  # некоторые прошивки не поддерживают один из каналов

        await asyncio.sleep(0.3)  # пауза до инициализационного handshake

        # Трёхэтапное рукопожатие
        self._seq = 0
        await self.write_raw(_cmd_init(self._next_seq()))
        await self.write_raw(_cmd_device_info(self._next_seq()))
        await self.write_raw(_cmd_state_query(self._next_seq()))
        logger.info("Surplife: подключено к %s", self.device_name)

    async def disconnect(self) -> None:
        """Разорвать BLE-соединение (если оно активно)."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
            logger.info("Surplife: отключено")

    # ── Низкоуровневая запись ──────────────────────────────────────────────

    async def write_raw(self, data: bytes) -> None:
        """
        Записать байты напрямую в характеристику FF01 (без подтверждения).

        После каждой записи делается пауза 0.3 с — лампа работает медленно
        и не успевает обработать команды без задержки между ними.

        response=False — write without response (BLE write command, не write request).
        """
        if not self.is_connected:
            raise RuntimeError("Нет соединения. Вызовите connect() сначала.")
        await self._client.write_gatt_char(FF01, data, response=False)
        await asyncio.sleep(0.3)

    # ── Высокоуровневые команды ────────────────────────────────────────────

    async def turn_on(self) -> None:
        """Включить питание лампы."""
        await self.write_raw(_cmd_power_on(self._next_seq()))
        logger.info("Surplife: включено")

    async def turn_off(self) -> None:
        """Выключить питание лампы."""
        await self.write_raw(_cmd_power_off(self._next_seq()))
        logger.info("Surplife: выключено")

    async def set_color(self, r: int, g: int, b: int) -> None:
        """
        Установить цвет через RGB (0-255).

        RGB автоматически конвертируется в HSV (используется нативная модель Surplife).
        Если насыщенность == 0 (оттенки серого, белый, чёрный) — активируется
        белый режим вместо цветного.

        Пример:
            await lamp.set_color(255, 128, 0)   # оранжевый
            await lamp.set_color(255, 255, 255) # белый (→ белый режим, CCT 50%)
        """
        r = max(0, min(255, r))
        g = max(0, min(255, g))
        b = max(0, min(255, b))
        h, s, v = rgb_to_hsv(r, g, b)
        if s == 0:
            # Ахроматический цвет → белый режим с соответствующей яркостью
            self._last_mode = "white"
            self._last_bright = v
            await self.write_raw(_cmd_set_white(self._next_seq(), v))
        else:
            self._last_mode = "hsv"
            self._last_hue = h
            self._last_sat = s
            self._last_bright = v
            await self.write_raw(_cmd_set_color(self._next_seq(), h, s, v))
        logger.info("Surplife: цвет rgb(%d,%d,%d) → hsv(%d,%d,%d)", r, g, b, h, s, v)

    async def set_brightness(self, percent: int) -> None:
        """
        Установить яркость (0-100%), сохраняя текущий режим и цвет.

        Если лампа в цветном режиме (HSV) — посылает HSV-команду с новой яркостью,
        сохраняя оттенок и насыщенность.
        Если лампа в белом режиме — посылает white-команду с сохранённой CCT.
        """
        percent = max(0, min(100, percent))
        self._last_bright = percent
        if self._last_mode == "hsv":
            await self.write_raw(_cmd_set_color(
                self._next_seq(), self._last_hue, self._last_sat, percent
            ))
            logger.info("Surplife: яркость %d%% (HSV h=%d s=%d)", percent,
                        self._last_hue, self._last_sat)
        else:
            await self.write_raw(_cmd_set_white(self._next_seq(), percent, self._last_cct))
            logger.info("Surplife: яркость %d%% (white CCT=%d)", percent, self._last_cct)

    async def set_color_temperature(self, kelvin: int) -> None:
        """
        Установить цветовую температуру в диапазоне 2700–6500 K.

        Кельвины линейно пересчитываются в CCT (0-100%):
          CCT = (kelvin - 2700) / (6500 - 2700) * 100

        Яркость в этом режиме фиксируется на 100% (максимум).
        Для одновременного управления яркостью используйте set_white().
        """
        cct = round((kelvin - 2700) / (6500 - 2700) * 100)
        cct = max(0, min(100, cct))
        self._last_mode = "white"
        self._last_cct = cct
        self._last_bright = 100
        await self.write_raw(_cmd_set_white(self._next_seq(), 100, cct))
        logger.info("Surplife: CCT %d K → %d%%", kelvin, cct)

    async def set_white(self, brightness: int = 100, color_temp: int = 50) -> None:
        """
        Прямое управление белым режимом (яркость + цветовая температура).

        brightness  — яркость 0-100%
        color_temp  — цветовая температура:
                      0   = тёплый белый (≈2700 K)
                      50  = нейтральный  (≈4600 K)  ← по умолчанию
                      100 = холодный     (≈6500 K)
        """
        self._last_mode = "white"
        self._last_bright = brightness
        self._last_cct = color_temp
        await self.write_raw(_cmd_set_white(self._next_seq(), brightness, color_temp))
        logger.info("Surplife: белый %d%% CCT=%d%%", brightness, color_temp)

    async def set_color_hsv(self, hue: int,
                             saturation: int = 100, brightness: int = 100) -> None:
        """
        Установить цвет напрямую через HSV (без RGB-конвертации).

        hue        — оттенок 0-359° (лампа принимает hue//2 внутри)
        saturation — насыщенность 0-100% (по умолчанию максимум)
        brightness — яркость 0-100% (по умолчанию максимум)

        Пример:
            await lamp.set_color_hsv(0,   100, 80)  # красный, 80% яркости
            await lamp.set_color_hsv(120, 100, 80)  # зелёный, 80% яркости
            await lamp.set_color_hsv(240, 100, 80)  # синий,   80% яркости
        """
        self._last_mode = "hsv"
        self._last_hue = hue
        self._last_sat = saturation
        self._last_bright = brightness
        await self.write_raw(_cmd_set_color(self._next_seq(), hue, saturation, brightness))
        logger.info("Surplife: HSV(%d°, %d%%, %d%%)", hue, saturation, brightness)

    # ── Инспекция ──────────────────────────────────────────────────────────

    async def dump_services(self) -> None:
        """
        Вывести все GATT-сервисы и характеристики подключённого устройства.

        Полезно для отладки и реверс-инжиниринга прошивок.
        """
        if not self.is_connected:
            raise RuntimeError("Нет соединения.")
        print(f"\n=== GATT-сервисы {self.address} ===\n")
        for service in self._client.services:
            print(f"[Сервис] {service.uuid}  —  {service.description}")
            for char in service.characteristics:
                props = ", ".join(char.properties)
                print(f"    [Хар-ка] {char.uuid}  props=[{props}]  —  {char.description}")
        print()
