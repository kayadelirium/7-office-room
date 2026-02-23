"""speaker — подключение Bluetooth-колонки и переключение аудиовыхода.

Для подключения использует системные утилиты:
  macOS  — blueutil    (brew install blueutil)
  Linux  — bluetoothctl (входит в BlueZ, обычно уже установлен)
  Windows — BT-подключение не поддерживается; переключение аудио работает.

Переключение аудиовыхода Silero TTS выполняется через sounddevice (PortAudio)
и работает кроссплатформенно — системный выход при этом не меняется.
"""

from __future__ import annotations

import asyncio
import json
import platform
import re
from typing import Optional

import sounddevice as sd


# ─── Конфигурация ─────────────────────────────────────────────────────────────

_mac:  str = ""   # MAC-адрес BT-колонки (AA:BB:CC:DD:EE:FF)
_name: str = ""   # подстрока имени для поиска в списке аудиоустройств


def configure(mac: str = "", name: str = "") -> None:
    """Задать MAC и имя колонки (вызывается из async_main при старте)."""
    global _mac, _name
    _mac  = mac.strip()
    _name = name.strip()


def is_configured() -> bool:
    return bool(_mac or _name)


# ─── Внутренние утилиты ───────────────────────────────────────────────────────

async def _run(*args: str) -> bool:
    """Запустить subprocess и вернуть True если завершился с кодом 0."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=15)
        return proc.returncode == 0
    except (FileNotFoundError, asyncio.TimeoutError):
        return False


def _find_output_device(name: str) -> Optional[int]:
    """Найти индекс выходного аудиоустройства по подстроке имени."""
    for i, dev in enumerate(sd.query_devices()):
        if name.lower() in dev["name"].lower() and dev["max_output_channels"] > 0:
            return i
    return None


def _current_input_device() -> object:
    dev = sd.default.device
    return dev[0] if isinstance(dev, (list, tuple)) else dev


# ─── Публичный API ────────────────────────────────────────────────────────────

async def connect() -> tuple[bool, str]:
    """Подключить BT-колонку и переключить аудиовыход sounddevice на неё.

    Возвращает (успех, сообщение).
    """
    if not is_configured():
        return False, "MAC-адрес или имя колонки не заданы. Укажите --speaker-mac и --speaker-name."

    # 1. Установить BT-соединение
    bt_ok = True
    if _mac:
        system = platform.system()
        if system == "Darwin":
            bt_ok = await _run("blueutil", "--connect", _mac)
            if not bt_ok:
                return False, "Не удалось подключиться. Убедитесь что blueutil установлен: brew install blueutil"
        elif system == "Linux":
            bt_ok = await _run("bluetoothctl", "connect", _mac)
            if not bt_ok:
                return False, "Не удалось подключиться. Проверьте, что bluetoothctl доступен."
        else:
            return False, "Управление BT-подключением на Windows не поддерживается. Подключите колонку вручную."

    # 2. Дать системе зарегистрировать устройство как аудиовыход
    if bt_ok and _name:
        for _ in range(6):
            idx = _find_output_device(_name)
            if idx is not None:
                sd.default.device = (_current_input_device(), idx)
                return True, f"Подключено. Аудио переключено на {sd.query_devices(idx)['name']}."
            await asyncio.sleep(1)
        return True, "BT подключён, но устройство не найдено в списке аудиовыходов."

    return bt_ok, "Подключено." if bt_ok else "Ошибка подключения."


async def connect_by_name(name: str) -> tuple[bool, str]:
    """Найти BT-устройство по имени, подключиться и переключить аудиовыход.

    Алгоритм:
      1. Сканирует классические BT-устройства поблизости.
      2. Ищет устройство с именем, содержащим <name> (без учёта регистра).
      3. Подключается к найденному MAC.
      4. Ждёт появления устройства в списке аудиовыходов и переключает на него.
    """
    system = platform.system()
    if system not in ("Darwin", "Linux"):
        return False, "Подключение по имени на Windows не поддерживается. Подключите колонку вручную."

    # 1. Сканирование
    devices = await scan(timeout=8.0)
    match = next((d for d in devices if name.lower() in d["name"].lower()), None)
    if match is None:
        return False, f"Устройство с именем «{name}» не найдено. Попробуйте speaker scan."

    mac        = match["address"]
    found_name = match["name"] or mac

    # 2. BT-подключение
    if system == "Darwin":
        bt_ok = await _run("blueutil", "--connect", mac)
        if not bt_ok:
            return False, f"Не удалось подключиться к {found_name}. Проверьте blueutil: brew install blueutil"
    else:
        bt_ok = await _run("bluetoothctl", "connect", mac)
        if not bt_ok:
            return False, f"Не удалось подключиться к {found_name}."

    # 3. Переключить аудиовыход — ищем по имени найденного устройства
    for _ in range(6):
        idx = _find_output_device(name) or _find_output_device(found_name)
        if idx is not None:
            sd.default.device = (_current_input_device(), idx)
            return True, f"Подключено к {found_name}. Аудио переключено."
        await asyncio.sleep(1)

    return True, f"Подключено к {found_name}, но аудиовыход не переключён автоматически."


async def disconnect() -> tuple[bool, str]:
    """Отключить BT-колонку и вернуть стандартный аудиовыход sounddevice."""
    # Сбросить аудиовыход к системному по умолчанию
    sd.default.device = (_current_input_device(), None)

    if _mac:
        system = platform.system()
        if system == "Darwin":
            ok = await _run("blueutil", "--disconnect", _mac)
        elif system == "Linux":
            ok = await _run("bluetoothctl", "disconnect", _mac)
        else:
            return True, "Аудио сброшено. BT-отключение на Windows — вручную."
        return ok, "Отключено." if ok else "Аудио сброшено, но BT-отключение не удалось."

    return True, "Аудиовыход сброшен."


async def scan(timeout: float = 10.0) -> list[dict]:
    """Сканировать классические BT-устройства поблизости.

    Возвращает список словарей с ключами 'address' и 'name'.

    macOS  — blueutil --inquiry --format json
    Linux  — bluetoothctl (современный BlueZ; hcitool устарел и удалён в Ubuntu 22.04+)
    Windows — не поддерживается.
    """
    system = platform.system()
    devices: list[dict] = []

    if system == "Darwin":
        try:
            proc = await asyncio.create_subprocess_exec(
                "blueutil", "--inquiry", str(int(timeout)), "--format", "json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout + 5)
            except asyncio.TimeoutError:
                proc.kill()
                return devices
            if proc.returncode == 0 and stdout:
                for item in json.loads(stdout.decode()):
                    devices.append({
                        "address": item.get("address", ""),
                        "name":    item.get("name", ""),
                    })
        except FileNotFoundError:
            print("blueutil не найден. Установите: brew install blueutil")

    elif system == "Linux":
        # bluetoothctl — стандарт BlueZ, доступен на всех дистрибутивах.
        # hcitool устарел и удалён начиная с BlueZ 5.56 (Ubuntu 22.04+, Fedora 35+).
        try:
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            proc.stdin.write(b"scan on\n")
            await proc.stdin.drain()
            await asyncio.sleep(timeout)
            proc.stdin.write(b"devices\nquit\n")
            await proc.stdin.drain()
            proc.stdin.close()
            try:
                out = await asyncio.wait_for(proc.stdout.read(), timeout=5)
                await proc.wait()
            except asyncio.TimeoutError:
                proc.kill()
                out = b""
            seen: set[str] = set()
            for line in out.decode().splitlines():
                # Строки вида: "[NEW] Device XX:XX:XX:XX:XX:XX Name"
                #              "Device XX:XX:XX:XX:XX:XX Name"
                m = re.search(r"Device\s+([0-9A-Fa-f:]{17})\s+(.*)", line)
                if m:
                    addr = m.group(1).upper()
                    if addr not in seen:
                        seen.add(addr)
                        devices.append({"address": addr, "name": m.group(2).strip()})
        except FileNotFoundError:
            print("bluetoothctl не найден. Установите пакет bluez.")

    else:
        print("Сканирование BT-устройств на Windows не поддерживается.")

    return devices
