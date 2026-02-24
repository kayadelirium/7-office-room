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


def _find_output_device(name: str) -> int | None:
    """Найти индекс выходного аудиоустройства по подстроке имени."""
    for i, dev in enumerate(sd.query_devices()):
        if name.lower() in dev["name"].lower() and dev["max_output_channels"] > 0:
            return i
    return None


def _current_input_device() -> object:
    dev = sd.default.device
    return dev[0] if isinstance(dev, (list, tuple)) else dev


async def _bt_connect(mac: str, label: str = "") -> tuple[bool, str]:
    """BT-подключение через blueutil (macOS) или bluetoothctl (Linux)."""
    system = platform.system()
    suffix = f" к {label}" if label else ""
    if system == "Darwin":
        if await _run("blueutil", "--connect", mac):
            return True, ""
        return False, f"Не удалось подключиться{suffix}. Проверьте blueutil: brew install blueutil"
    elif system == "Linux":
        if await _run("bluetoothctl", "connect", mac):
            return True, ""
        return False, f"Не удалось подключиться{suffix}."
    return False, "BT-подключение на Windows не поддерживается. Подключите устройство вручную."


async def _bt_disconnect(mac: str) -> tuple[bool, str]:
    """BT-отключение через blueutil (macOS) или bluetoothctl (Linux)."""
    system = platform.system()
    if system == "Darwin":
        ok = await _run("blueutil", "--disconnect", mac)
    elif system == "Linux":
        ok = await _run("bluetoothctl", "disconnect", mac)
    else:
        return True, "Аудио сброшено. BT-отключение на Windows — вручную."
    return ok, "Отключено." if ok else "Аудио сброшено, но BT-отключение не удалось."


async def _switch_audio(*names: str) -> int | None:
    """Ждёт до 6 сек появления аудиоустройства и переключает на него."""
    for _ in range(6):
        for name in names:
            if name:
                idx = _find_output_device(name)
                if idx is not None:
                    sd.default.device = (_current_input_device(), idx)
                    return idx
        await asyncio.sleep(1)
    return None


# ─── Публичный API ────────────────────────────────────────────────────────────

async def connect() -> tuple[bool, str]:
    """Подключить BT-колонку и переключить аудиовыход sounddevice на неё.

    Возвращает (успех, сообщение).
    """
    if not is_configured():
        return False, "MAC-адрес или имя колонки не заданы. Укажите --speaker-mac и --speaker-name."

    if _mac:
        ok, err = await _bt_connect(_mac)
        if not ok:
            return False, err

    if _name:
        idx = await _switch_audio(_name)
        if idx is not None:
            return True, f"Подключено. Аудио переключено на {sd.query_devices(idx)['name']}."
        return True, "BT подключён, но устройство не найдено в списке аудиовыходов."

    return True, "Подключено."


async def connect_by_name(name: str) -> tuple[bool, str]:
    """Найти BT-устройство по имени, подключиться и переключить аудиовыход.

    Алгоритм:
      1. Сканирует классические BT-устройства поблизости.
      2. Ищет устройство с именем, содержащим <name> (без учёта регистра).
      3. Подключается к найденному MAC.
      4. Ждёт появления устройства в списке аудиовыходов и переключает на него.
    """
    if platform.system() not in ("Darwin", "Linux"):
        return False, "Подключение по имени на Windows не поддерживается. Подключите колонку вручную."

    devices = await scan(timeout=8.0)
    match = next((d for d in devices if name.lower() in d["name"].lower()), None)
    if match is None:
        return False, f"Устройство с именем «{name}» не найдено. Попробуйте speaker scan."

    mac        = match["address"]
    found_name = match["name"] or mac

    ok, err = await _bt_connect(mac, found_name)
    if not ok:
        return False, err

    idx = await _switch_audio(name, found_name)
    if idx is not None:
        return True, f"Подключено к {found_name}. Аудио переключено."
    return True, f"Подключено к {found_name}, но аудиовыход не переключён автоматически."


async def disconnect() -> tuple[bool, str]:
    """Отключить BT-колонку и вернуть стандартный аудиовыход sounddevice."""
    sd.default.device = (_current_input_device(), None)

    if _mac:
        return await _bt_disconnect(_mac)

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
