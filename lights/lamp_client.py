"""
lamp_client.py — библиотека для управления BLE-лампами.

─────────────────────────────────────────────────────────
ПОДДЕРЖИВАЕМЫЕ ПРОТОКОЛЫ
─────────────────────────────────────────────────────────
  1. Magic Home / Tuya / большинство китайских RGB-ламп
       Сервис: FFD5 | Запись: FFD9 | Notify: FFD4
       Команды: 0x56 (цвет), 0x61 (эффект), 0x71 (питание)

  2. Govee (упрощённый вариант)
       Сервис: 00010203-... | Запись: ...2b11
       Команды: 0x33 (питание), 0x35 (яркость/цвет)

  3. Nordic UART Service (NUS) — DIY-лампы на nRF5x
       Сервис: 6e400001-... | RX (запись): ...0002 | TX (notify): ...0003
       Команды: текст ("ON\r\n", "OFF\r\n")

  4. Triones / Triones-совместимые лампы
       Сервис: FFFF | Запись: FF01 | Notify: FF02
       Команды: 0x56 (цвет), 0xBB (эффект), 0xCC (питание)

  5. HappyLighting / ELK-BLEDOM
       Сервис: FE00 | Запись: FF11 | Notify: FF22
       Команды: 0x7e-формат

  6. Surplife — см. surplife_client.py
       Использует собственный seq-заголовок, отдельный класс.

─────────────────────────────────────────────────────────
АРХИТЕКТУРА КЛАССОВ
─────────────────────────────────────────────────────────
  AbstractLampClient (ABC)
      │  Общий интерфейс: turn_on/off, set_color, set_brightness,
      │  set_color_temperature, write_raw, connect/disconnect.
      │  Реализует context manager (__aenter__/__aexit__).
      │
      ├── BLELampClient
      │       Универсальный клиент. Настраивается через LampConfig.
      │       Команды строятся методами _build_color_cmd/_build_brightness_cmd
      │       (можно переопределять в подклассах).
      │
      ├── TrionesLampClient(BLELampClient)
      │       Переопределяет _build_* и set_effect для протокола Triones.
      │
      ├── HappyLightingLampClient(BLELampClient)
      │       Переопределяет _build_* и set_effect для протокола HappyLighting.
      │
      └── SurplifeLampClient  (в surplife_client.py)
              Полностью отдельная реализация с seq-счётчиком и 8-байтным заголовком.

─────────────────────────────────────────────────────────
БЫСТРЫЙ СТАРТ
─────────────────────────────────────────────────────────
    from lamp_client import make_client

    async with make_client("AA:BB:CC:DD:EE:FF", "magic_home") as lamp:
        await lamp.turn_on()
        await lamp.set_color(255, 0, 128)   # малиновый
        await lamp.set_brightness(80)       # 80%

Для Surplife использовать surplife_client.SurplifeLampClient напрямую.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Awaitable

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# UUID GATT-характеристик известных протоколов
# ---------------------------------------------------------------------------
# Bluetooth SIG назначает стандартные UUID для общеизвестных профилей.
# Производители ламп либо используют короткий UUID (0xFFD5 и т.п.),
# который разворачивается в полный вид 0000XXXX-0000-1000-8000-00805f9b34fb,
# либо задают полностью произвольный UUID (как у Govee).

# Magic Home / Triones / большинство китайских RGB-ламп
MAGIC_HOME_SERVICE_UUID  = "0000ffd5-0000-1000-8000-00805f9b34fb"
MAGIC_HOME_WRITE_UUID    = "0000ffd9-0000-1000-8000-00805f9b34fb"
MAGIC_HOME_NOTIFY_UUID   = "0000ffd4-0000-1000-8000-00805f9b34fb"

# Govee — использует нестандартный vendor UUID
GOVEE_SERVICE_UUID       = "00010203-0405-0607-0809-0a0b0c0d1910"
GOVEE_WRITE_UUID         = "00010203-0405-0607-0809-0a0b0c0d2b11"

# Generic "Nordic UART Service" — стандарт Nordic Semiconductor для UART поверх BLE
# Широко используется в DIY-проектах (ESPHome, Arduino BLE и т.д.)
NUS_SERVICE_UUID         = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_UUID              = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # write (host → lamp)
NUS_TX_UUID              = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # notify (lamp → host)

# Triones — используют ту же структуру команд, что Magic Home, но другой сервис
TRIONES_SERVICE_UUID     = "0000ffff-0000-1000-8000-00805f9b34fb"
TRIONES_WRITE_UUID       = "0000ff01-0000-1000-8000-00805f9b34fb"
TRIONES_NOTIFY_UUID      = "0000ff02-0000-1000-8000-00805f9b34fb"

# HappyLighting / ELK-BLEDOM — другой формат команд (0x7e-обёртка)
HAPPY_SERVICE_UUID       = "0000fe00-0000-1000-8000-00805f9b34fb"
HAPPY_WRITE_UUID         = "0000ff11-0000-1000-8000-00805f9b34fb"
HAPPY_NOTIFY_UUID        = "0000ff22-0000-1000-8000-00805f9b34fb"


# ---------------------------------------------------------------------------
# Конфигурация протокола (dataclass)
# ---------------------------------------------------------------------------

@dataclass
class LampConfig:
    """
    Описание протокола конкретной лампы.

    Хранит UUID сервиса/характеристик и байтовые команды включения/выключения.
    Передаётся в BLELampClient вместо пресета, когда нужна тонкая настройка.

    Поля:
      service_uuid   — UUID GATT-сервиса (для фильтрации при сканировании)
      write_uuid     — UUID характеристики для записи команд (write without response)
      notify_uuid    — UUID характеристики для уведомлений (может быть None)
      cmd_on         — байты команды включения питания
      cmd_off        — байты команды выключения питания
      use_response   — True → write_gatt_char(response=True), ждём подтверждения
    """
    service_uuid: str
    write_uuid: str
    notify_uuid: str | None = None
    cmd_on:  bytes = bytes([0x71, 0x23, 0x0F])   # Magic Home: power on
    cmd_off: bytes = bytes([0x71, 0x24, 0x0F])   # Magic Home: power off
    use_response: bool = False


# ---------------------------------------------------------------------------
# Словарь предустановленных конфигов (пресеты)
# ---------------------------------------------------------------------------

PRESETS: dict[str, LampConfig] = {
    # Magic Home — самый распространённый протокол для китайских RGB-ламп.
    # Характеристика FFD9 принимает команды длиной 3-7 байт.
    "magic_home": LampConfig(
        service_uuid=MAGIC_HOME_SERVICE_UUID,
        write_uuid=MAGIC_HOME_WRITE_UUID,
        notify_uuid=MAGIC_HOME_NOTIFY_UUID,
        cmd_on=bytes([0x71, 0x23, 0x0F]),
        cmd_off=bytes([0x71, 0x24, 0x0F]),
    ),
    # Govee использует 20-байтные команды с XOR-контрольной суммой в последнем байте.
    "govee": LampConfig(
        service_uuid=GOVEE_SERVICE_UUID,
        write_uuid=GOVEE_WRITE_UUID,
        cmd_on=bytes([0x33, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00,
                      0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                      0x00, 0x00, 0x00, 0x33]),   # последний байт = XOR всех предыдущих
        cmd_off=bytes([0x33, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                       0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                       0x00, 0x00, 0x00, 0x33]),
    ),
    # NUS — текстовый протокол. Команды — ASCII-строки с CRLF.
    "nus": LampConfig(
        service_uuid=NUS_SERVICE_UUID,
        write_uuid=NUS_RX_UUID,
        notify_uuid=NUS_TX_UUID,
        cmd_on=b"ON\r\n",
        cmd_off=b"OFF\r\n",
    ),
    # Triones совместимы с Magic Home по формату цветовых команд (0x56),
    # но используют другие UUID и другой формат команды эффекта (0xBB вместо 0x61).
    "triones": LampConfig(
        service_uuid=TRIONES_SERVICE_UUID,
        write_uuid=TRIONES_WRITE_UUID,
        notify_uuid=TRIONES_NOTIFY_UUID,
        cmd_on=bytes([0xCC, 0x23, 0x33]),
        cmd_off=bytes([0xCC, 0x24, 0x33]),
    ),
    # HappyLighting / ELK-BLEDOM — команды обёрнуты в 0x7e...0xef (7-9 байт).
    "happylighting": LampConfig(
        service_uuid=HAPPY_SERVICE_UUID,
        write_uuid=HAPPY_WRITE_UUID,
        notify_uuid=HAPPY_NOTIFY_UUID,
        cmd_on=bytes([0x7e, 0x00, 0x04, 0x01, 0x00, 0x00, 0x00, 0xff, 0x00]),
        cmd_off=bytes([0x7e, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00, 0xff, 0x00]),
    ),
}


# ---------------------------------------------------------------------------
# Абстрактный базовый класс (интерфейс)
# ---------------------------------------------------------------------------

class AbstractLampClient(ABC):
    """
    Общий интерфейс для всех клиентов BLE-ламп.

    Цель: main.py и другой код могут работать с любым типом лампы
    через единый набор методов, не зная деталей протокола.

    Обязательные методы (подклассы ОБЯЗАНЫ их реализовать):
      connect(scan_timeout)     — найти устройство и подключиться
      disconnect()              — разорвать соединение
      is_connected              — property, текущее состояние соединения
      write_raw(data)           — записать байты напрямую
      turn_on() / turn_off()    — питание
      set_color(r, g, b)        — цвет (RGB 0-255)
      set_brightness(percent)   — яркость (0-100%)
      set_color_temperature(K)  — цветовая температура в Кельвинах

    Опциональные методы (по умолчанию бросают NotImplementedError):
      set_effect(mode, speed)
      dump_services()
      read_characteristic(uuid)

    Context manager:
      __aenter__ вызывает connect(), __aexit__ вызывает disconnect().
      Реализован здесь один раз — подклассы его не переопределяют.
    """

    address: str  # MAC (Linux/Windows) или UUID (macOS); задаётся в __init__ подкласса

    # ── Context manager ────────────────────────────────────────────────────

    async def __aenter__(self) -> "AbstractLampClient":
        await self.connect()
        return self

    async def __aexit__(self, *_) -> None:
        await self.disconnect()

    # ── Обязательный интерфейс ─────────────────────────────────────────────

    @property
    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    async def connect(self, scan_timeout: float = 10.0) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def write_raw(self, data: bytes) -> None: ...

    @abstractmethod
    async def turn_on(self) -> None: ...

    @abstractmethod
    async def turn_off(self) -> None: ...

    @abstractmethod
    async def set_color(self, r: int, g: int, b: int) -> None: ...

    @abstractmethod
    async def set_brightness(self, percent: int) -> None: ...

    @abstractmethod
    async def set_color_temperature(self, kelvin: int) -> None: ...

    # ── Опциональные методы ────────────────────────────────────────────────

    async def set_effect(self, mode: int, speed: int = 0x50) -> None:
        raise NotImplementedError(f"{type(self).__name__} не поддерживает set_effect.")

    async def dump_services(self) -> None:
        raise NotImplementedError(f"{type(self).__name__} не поддерживает dump_services.")

    async def read_characteristic(self, uuid: str) -> bytes:
        raise NotImplementedError(f"{type(self).__name__} не поддерживает read_characteristic.")


# ---------------------------------------------------------------------------
# Универсальный клиент (Magic Home / Govee / NUS / Triones / HappyLighting)
# ---------------------------------------------------------------------------

class BLELampClient(AbstractLampClient):
    """
    Универсальный клиент для ламп с «плоским» протоколом (без seq-заголовка).

    Поведение определяется объектом LampConfig, переданным при создании.
    Конкретные форматы команд задаются методами _build_color_cmd и
    _build_brightness_cmd — их можно переопределить в подклассе.

    Пример:
        async with BLELampClient("AA:BB:CC:DD:EE:FF", preset="magic_home") as lamp:
            await lamp.turn_on()
            await lamp.set_color(255, 0, 128)
            await lamp.set_brightness(80)
    """

    def __init__(
        self,
        address: str,
        preset: str = "magic_home",
        config: LampConfig | None = None,
        on_notify: Callable[[bytes], Awaitable[None] | None] | None = None,
    ) -> None:
        """
        address   — MAC (Linux/Windows) или CoreBluetooth UUID (macOS)
        preset    — имя пресета из PRESETS (игнорируется, если задан config)
        config    — явная конфигурация протокола (переопределяет preset)
        on_notify — callback, вызываемый при получении notify-уведомления
        """
        self.address = address
        self.config: LampConfig = config or PRESETS[preset]
        self._on_notify = on_notify
        self._client: BleakClient | None = None
        self._last_r: int | None = None   # последний установленный RGB-цвет
        self._last_g: int | None = None
        self._last_b: int | None = None

    # ── Соединение ─────────────────────────────────────────────────────────

    async def connect(self, scan_timeout: float = 10.0) -> None:
        """
        Найти устройство через BLE-сканирование и подключиться к нему.

        На macOS CoreBluetooth не поддерживает прямое подключение по UUID —
        устройство должно быть обнаружено активным сканированием.
        BleakScanner.find_device_by_address() выполняет этот шаг автоматически.

        После подключения активируется подписка на notify-уведомления
        (если задан on_notify и notify_uuid в конфиге).
        """
        logger.info("Поиск %s (до %.0f сек)…", self.address, scan_timeout)
        device = await BleakScanner.find_device_by_address(
            self.address, timeout=scan_timeout
        )
        if device is None:
            raise ConnectionError(
                f"Устройство {self.address} не найдено. "
                "Убедитесь что лампа включена и находится рядом."
            )
        logger.info("Найдено: %s. Подключение…", device.name)
        self._client = BleakClient(device)
        await self._client.connect()
        if not self._client.is_connected:
            raise ConnectionError(f"Не удалось подключиться к {self.address}")
        logger.info("Подключено.")

        # Подписка на уведомления (опционально)
        if self.config.notify_uuid and self._on_notify:
            await self._client.start_notify(
                self.config.notify_uuid, self._notify_handler
            )
            logger.info("Подписка на уведомления включена.")

    async def disconnect(self) -> None:
        """Остановить notify-подписку и разорвать BLE-соединение."""
        if self._client and self._client.is_connected:
            if self.config.notify_uuid and self._on_notify:
                try:
                    await self._client.stop_notify(self.config.notify_uuid)
                except Exception:
                    pass  # игнорируем ошибки при остановке notify
            await self._client.disconnect()
            logger.info("Отключено.")

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    # ── Низкоуровневая запись ──────────────────────────────────────────────

    async def write_raw(self, data: bytes) -> None:
        """
        Записать произвольные байты в write-характеристику.

        Используется как basis для всех высокоуровневых команд,
        а также доступна напрямую через интерактивный режим ('write <hex>').

        use_response=False — write without response (быстрее, без ACK от лампы).
        use_response=True  — write with response (надёжнее, но медленнее).
        """
        if not self.is_connected:
            raise RuntimeError("Нет соединения. Вызовите connect() сначала.")
        logger.debug("→ write %s", data.hex())
        await self._client.write_gatt_char(  # type: ignore[union-attr]
            self.config.write_uuid,
            data,
            response=self.config.use_response,
        )

    def _notify_handler(self, char: BleakGATTCharacteristic, data: bytearray) -> None:
        """
        Обработчик входящих BLE-уведомлений.

        Bleak вызывает этот метод в основном event loop при получении
        пакета от лампы. Если on_notify возвращает coroutine — он планируется
        через ensure_future (не блокирует обработчик).
        """
        logger.debug("← notify %s: %s", char.uuid, bytes(data).hex())
        if self._on_notify:
            result = self._on_notify(bytes(data))
            if asyncio.iscoroutine(result):
                asyncio.ensure_future(result)

    # ── Высокоуровневые команды ────────────────────────────────────────────

    async def turn_on(self) -> None:
        """Включить лампу (отправляет cmd_on из конфига)."""
        await self.write_raw(self.config.cmd_on)
        logger.info("Лампа включена.")

    async def turn_off(self) -> None:
        """Выключить лампу (отправляет cmd_off из конфига)."""
        await self.write_raw(self.config.cmd_off)
        logger.info("Лампа выключена.")

    async def set_color(self, r: int, g: int, b: int) -> None:
        """
        Установить цвет лампы (RGB, 0–255).

        Формат Magic Home: [0x56, R, G, B, 0x00, 0xF0, 0xAA]
          0x56 — код команды цвета
          R G B — компоненты (0-255)
          0x00  — канал белого (0 = не используется в режиме RGB)
          0xF0  — флаг: использовать RGB (0x0F = использовать White)
          0xAA  — маркер конца команды (некоторые прошивки игнорируют)
        """
        r, g, b = _clamp(r), _clamp(g), _clamp(b)
        self._last_r, self._last_g, self._last_b = r, g, b
        cmd = self._build_color_cmd(r, g, b)
        await self.write_raw(cmd)
        logger.info("Цвет: rgb(%d, %d, %d)", r, g, b)

    async def set_brightness(self, percent: int) -> None:
        """
        Установить яркость (0–100%), сохраняя текущий цвет.

        Если до этого был установлен RGB-цвет, масштабирует его компоненты
        по коэффициенту яркости, сохраняя оттенок и насыщенность.
        Если цвет ещё не задан — управляет White-каналом (исходное поведение).

        Формат Magic Home White: [0x56, 0x00, 0x00, 0x00, W, 0x0F, 0xAA]
        """
        percent = max(0, min(100, percent))
        if self._last_r is not None:
            factor = percent / 100
            r = round(self._last_r * factor)
            g = round(self._last_g * factor)  # type: ignore[operator]
            b = round(self._last_b * factor)  # type: ignore[operator]
            cmd = self._build_color_cmd(r, g, b)
        else:
            value = round(percent * 255 / 100)
            cmd = self._build_brightness_cmd(value)
        await self.write_raw(cmd)
        logger.info("Яркость: %d%%", percent)

    async def set_color_temperature(self, kelvin: int) -> None:
        """
        Установить цветовую температуру (2700–6500 K).

        Аппроксимируется через RGB: конвертируем Кельвины в RGB
        с помощью алгоритма Tanner Helland и отправляем как цвет.
        Лампа должна поддерживать RGB-режим (не все поддерживают CCT напрямую).
        """
        r, g, b = _kelvin_to_rgb(kelvin)
        await self.set_color(r, g, b)

    async def set_effect(self, mode: int, speed: int = 0x50) -> None:
        """
        Включить встроенный световой эффект.

        Формат Magic Home: [0x61, mode, speed, 0x0F]
          mode  — номер эффекта (0x25–0x38: различные режимы мигания/переливов)
          speed — скорость (0x01 = максимальная, 0xFF = минимальная)
          0x0F  — фиксированный маркер команды эффекта
        """
        cmd = bytes([0x61, mode, speed, 0x0F])
        await self.write_raw(cmd)
        logger.info("Эффект 0x%02x, скорость 0x%02x", mode, speed)

    # ── Построение команд (можно переопределить в подклассах) ──────────────

    def _build_color_cmd(self, r: int, g: int, b: int) -> bytes:
        """Magic Home RGB-команда. Переопределяется в TrionesLampClient и др."""
        return bytes([0x56, r, g, b, 0x00, 0xF0, 0xAA])

    def _build_brightness_cmd(self, value: int) -> bytes:
        """Magic Home White-команда. Переопределяется в HappyLightingLampClient и др."""
        return bytes([0x56, 0x00, 0x00, 0x00, value, 0x0F, 0xAA])

    # ── Инспекция устройства ───────────────────────────────────────────────

    async def dump_services(self) -> None:
        """Вывести все GATT-сервисы и характеристики подключённого устройства."""
        if not self.is_connected:
            raise RuntimeError("Нет соединения.")
        print(f"\n=== GATT-сервисы {self.address} ===\n")
        for service in self._client.services:  # type: ignore[union-attr]
            print(f"[Сервис] {service.uuid}  —  {service.description}")
            for char in service.characteristics:
                props = ", ".join(char.properties)
                print(f"    [Хар-ка] {char.uuid}  props=[{props}]  —  {char.description}")
        print()

    async def read_characteristic(self, uuid: str) -> bytes:
        """Прочитать значение GATT-характеристики по её UUID."""
        if not self.is_connected:
            raise RuntimeError("Нет соединения.")
        data = await self._client.read_gatt_char(uuid)  # type: ignore[union-attr]
        logger.info("Read %s → %s", uuid, bytes(data).hex())
        return bytes(data)


# ---------------------------------------------------------------------------
# Вспомогательные функции (приватные)
# ---------------------------------------------------------------------------

def _clamp(v: int) -> int:
    """Ограничить значение диапазоном 0-255."""
    return max(0, min(255, v))


def _kelvin_to_rgb(kelvin: int) -> tuple[int, int, int]:
    """
    Приближённо перевести цветовую температуру в RGB.

    Алгоритм Tanner Helland (2012) — широко используемая аппроксимация,
    точная в диапазоне 1000–40000 K с погрешностью ~10 единиц на канал.
    Источник: https://tannerhelland.com/2012/09/18/convert-temperature-rgb-algorithm-code.html
    """
    import math
    kelvin = max(1000, min(40000, kelvin))
    temp = kelvin / 100.0

    # ── Красный ────────────────────────────────────────────────────────────
    # До 6600 K красный компонент максимален (тёплый свет).
    # Выше 6600 K плавно уменьшается (переход к синеватому/белому).
    if temp <= 66:
        r = 255
    else:
        r = 329.698727446 * ((temp - 60) ** -0.1332047592)

    # ── Зелёный ────────────────────────────────────────────────────────────
    # Логарифмическая зависимость в обоих диапазонах.
    if temp <= 66:
        g = 99.4708025861 * math.log(temp) - 161.1195681661
    else:
        g = 288.1221695283 * ((temp - 60) ** -0.0755148492)

    # ── Синий ──────────────────────────────────────────────────────────────
    # Выше 6600 K синий максимален. Ниже 1900 K синего нет вообще.
    if temp >= 66:
        b = 255
    elif temp <= 19:
        b = 0
    else:
        b = 138.5177312231 * math.log(temp - 10) - 305.0447927307

    return _clamp(int(r)), _clamp(int(g)), _clamp(int(b))


# ---------------------------------------------------------------------------
# Утилиты цвета (публичные — используются в surplife_client и main.py)
# ---------------------------------------------------------------------------

def parse_hex_color(hex_str: str) -> tuple[int, int, int]:
    """
    Разобрать строку '#RRGGBB' или 'RRGGBB' и вернуть (R, G, B).

    Пример:
        parse_hex_color("#FF8000")  →  (255, 128, 0)
        parse_hex_color("FF8000")   →  (255, 128, 0)
    """
    h = hex_str.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"Неверный hex: {hex_str!r}. Ожидается RRGGBB.")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hsv(r: int, g: int, b: int) -> tuple[int, int, int]:
    """
    Конвертировать RGB (0-255) в HSV (hue 0-359°, sat 0-100%, val 0-100%).

    Используется для Surplife, который управляет цветом через HSV-модель.

    Ахроматические цвета (r == g == b, или sat < 3%) возвращают (0, 0, val),
    что сигнализирует: нужно использовать белый режим, не цветной.

    Алгоритм:
      1. Нормализуем RGB в [0,1].
      2. max(r,g,b) = value.
      3. Насыщенность = (max - min) / max.
      4. Оттенок зависит от того, какой канал максимальный.
    """
    if r == g == b:
        return 0, 0, round(r / 255 * 100)
    rf, gf, bf = r / 255, g / 255, b / 255
    mx, mn = max(rf, gf, bf), min(rf, gf, bf)
    v = mx
    s = (mx - mn) / mx
    # Вычисляем оттенок в зависимости от доминирующего канала
    if   mx == rf: h = (60 * ((gf - bf) / (mx - mn))) % 360
    elif mx == gf: h = 60 * ((bf - rf) / (mx - mn)) + 120
    else:          h = 60 * ((rf - gf) / (mx - mn)) + 240
    sat = round(s * 100)
    val = round(v * 100)
    # Очень слабая насыщенность или яркость → считаем ахроматическим
    if sat < 3 or val < 1:
        return 0, 0, val
    return int(h), sat, val


def hsv_to_rgb_255(h: int, s: int, v: int) -> tuple[int, int, int]:
    """
    Конвертировать HSV (0-360°, 0-100%, 0-100%) в RGB (0-255).

    Используется для отображения цветного свотча в терминале.

    Алгоритм: стандартное деление цветового круга на 6 секторов.
      C = V * S  (chroma — «насыщенная» составляющая)
      X = C * (1 - |H/60 mod 2 - 1|)  (промежуточное значение)
      m = V - C  (смещение для добавления яркости)
    """
    s, v = s / 100, v / 100
    c = v * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = v - c
    if   h < 60:  r, g, b = c, x, 0
    elif h < 120: r, g, b = x, c, 0
    elif h < 180: r, g, b = 0, c, x
    elif h < 240: r, g, b = 0, x, c
    elif h < 300: r, g, b = x, 0, c
    else:         r, g, b = c, 0, x
    return int((r + m) * 255), int((g + m) * 255), int((b + m) * 255)


# ---------------------------------------------------------------------------
# Протокол-специфичные подклассы
# ---------------------------------------------------------------------------

class TrionesLampClient(BLELampClient):
    """
    Клиент для Triones-совместимых ламп (сервис 0xFFFF, характеристика 0xFF01).

    Формат цветовых команд совпадает с Magic Home (0x56...),
    но команда эффекта отличается: [0xBB, mode, speed, 0x44].
    """

    def __init__(self, address: str, **kwargs):
        super().__init__(address, config=PRESETS["triones"], **kwargs)

    def _build_color_cmd(self, r: int, g: int, b: int) -> bytes:
        # [0x56, R, G, B, 0x00, 0xF0, 0xAA] — идентично Magic Home
        return bytes([0x56, r, g, b, 0x00, 0xF0, 0xAA])

    def _build_brightness_cmd(self, value: int) -> bytes:
        # White-канал: [0x56, 0, 0, 0, W, 0x0F, 0xAA] — идентично Magic Home
        return bytes([0x56, 0x00, 0x00, 0x00, value, 0x0F, 0xAA])

    async def set_effect(self, mode: int, speed: int = 0x50) -> None:
        # Triones-формат эффекта: [0xBB, mode, speed, 0x44]
        # В отличие от Magic Home (0x61...0x0F), Triones использует 0xBB...0x44
        cmd = bytes([0xBB, mode, speed, 0x44])
        await self.write_raw(cmd)
        logger.info("Эффект 0x%02x, скорость 0x%02x", mode, speed)


class HappyLightingLampClient(BLELampClient):
    """
    Клиент для HappyLighting / ELK-BLEDOM ламп (сервис 0xFE00, характеристика 0xFF11).

    Команды обёрнуты в формат 0x7e ... 0xef (7-10 байт).
    Байт [1] = 0x00 (зарезервировано), [2] = код функции:
      0x01 — яркость
      0x03 — эффект
      0x04 — питание
      0x05 — цвет RGB
    """

    def __init__(self, address: str, **kwargs):
        super().__init__(address, config=PRESETS["happylighting"], **kwargs)

    def _build_color_cmd(self, r: int, g: int, b: int) -> bytes:
        # [0x7e, 0x00, 0x05, 0x03, R, G, B, 0x00, 0xef]
        #  0x05 = RGB-функция, 0x03 = подрежим, 0x00 = белый канал не задан
        return bytes([0x7e, 0x00, 0x05, 0x03, r, g, b, 0x00, 0xef])

    def _build_brightness_cmd(self, value: int) -> bytes:
        # [0x7e, 0x00, 0x01, value, 0x01, 0xff, 0xff, 0x00, 0xef]
        #  0x01 = яркость, value = 0-255, 0x01 = включено
        return bytes([0x7e, 0x00, 0x01, value, 0x01, 0xff, 0xff, 0x00, 0xef])

    async def set_brightness(self, percent: int) -> None:
        """HappyLighting имеет нативную команду яркости — не меняет цвет напрямую."""
        percent = max(0, min(100, percent))
        value = round(percent * 255 / 100)
        await self.write_raw(self._build_brightness_cmd(value))
        logger.info("Яркость: %d%%", percent)

    async def set_effect(self, mode: int, speed: int = 0x50) -> None:
        # [0x7e, 0x00, 0x03, mode, speed, 0x00, 0x00, 0xff, 0x00, 0xef]
        #  0x03 = эффект, 0xff в позиции [7] — маркер
        cmd = bytes([0x7e, 0x00, 0x03, mode, speed, 0x00, 0x00, 0xff, 0x00, 0xef])
        await self.write_raw(cmd)
        logger.info("Эффект 0x%02x, скорость 0x%02x", mode, speed)


# Словарь: пресет → специализированный класс.
# Пресеты, не перечисленные здесь, будут обслуживаться базовым BLELampClient.
_PRESET_CLASSES: dict[str, type[BLELampClient]] = {
    "triones":       TrionesLampClient,
    "happylighting": HappyLightingLampClient,
}


def make_client(address: str, preset: str, **kwargs) -> BLELampClient:
    """
    Фабричная функция: создать правильный клиент по имени пресета.

    Если для пресета есть специализированный класс — использует его.
    Иначе создаёт BLELampClient с конфигом из PRESETS[preset].

    Kwargs передаются в конструктор (например, on_notify=callback).
    """
    cls = _PRESET_CLASSES.get(preset, BLELampClient)
    if cls is BLELampClient:
        return BLELampClient(address, config=PRESETS[preset], **kwargs)
    return cls(address, **kwargs)


# ---------------------------------------------------------------------------
# Поиск устройства по имени
# ---------------------------------------------------------------------------

async def find_lamp(name_substring: str, timeout: float = 10.0) -> str | None:
    """
    Найти BLE-лампу по подстроке имени через активное сканирование.

    Возвращает MAC/UUID первого найденного устройства или None.
    Удобно когда адрес неизвестен, но имя устройства известно.

    Пример:
        address = await find_lamp("Lamp")
    """
    print(f'Поиск устройства "{name_substring}" ({timeout} сек)…')
    device = await BleakScanner.find_device_by_filter(
        lambda d, _: d.name is not None and name_substring.lower() in d.name.lower(),
        timeout=timeout,
    )
    if device:
        print(f"Найдено: {device.name}  [{device.address}]")
        return device.address
    print("Устройство не найдено.")
    return None
