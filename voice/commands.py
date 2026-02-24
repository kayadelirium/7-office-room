"""commands.py — Разбор и выполнение голосовых команд лампы."""

from __future__ import annotations

import asyncio

from lights import (
    AbstractLampClient, SurplifeLampClient,
    make_client, find_lamp,
    parse_hex_color, rgb_to_hsv,
)
from scanner import scan as ble_scan
from voice.tts import Voice, speak, do_and_speak
import speaker


# ─── Лампы по умолчанию ───────────────────────────────────────────────────────
#
# Перебираются при команде «включи» / «дефолтная лампа» — подключаются все.
# Поля:
#   name    — подстрока имени для BLE-поиска (используется если address пуст)
#   preset  — протокол подключения
#   address — MAC (Linux: AA:BB:CC:DD:EE:FF) или UUID (macOS: XXXXXXXX-XXXX-...)
#             Если задан — подключаемся напрямую, минуя сканирование.

DEFAULT_LAMPS: list[dict] = [
    {"name": "ELK-BLEDOM", "preset": "elk_bledom",  "address": ""},
    {"name": "IOTBT5AB",   "preset": "surplife",    "address": ""},
]


# ─── Создание клиента ─────────────────────────────────────────────────────────

def make_lamp(address: str, preset: str) -> AbstractLampClient:
    if preset == "surplife":
        return SurplifeLampClient(address)
    return make_client(address, preset)


# ─── Состояние лампы ──────────────────────────────────────────────────────────

def _hue_to_name(hue: int) -> str:
    """Примерное название цвета по оттенку (hue 0-359°)."""
    h = hue % 360
    if h < 15 or h >= 345: return "красный"
    if h < 45:              return "оранжевый"
    if h < 75:              return "жёлтый"
    if h < 150:             return "зелёный"
    if h < 195:             return "голубой"
    if h < 255:             return "синий"
    if h < 285:             return "фиолетовый"
    return "малиновый"


def lamp_status_text(lamp: AbstractLampClient) -> str:
    """Текстовое описание текущего состояния лампы для вывода и TTS."""
    if isinstance(lamp, SurplifeLampClient):
        mode   = lamp._last_mode
        bright = lamp._last_bright
        if mode == "hsv":
            hue  = lamp._last_hue
            sat  = lamp._last_sat
            name = _hue_to_name(hue)
            if sat < 20:
                return f"Белый цвет, яркость {bright} процентов."
            return f"{name.capitalize()}, насыщенность {sat} процентов, яркость {bright} процентов."
        else:  # white
            cct    = lamp._last_cct
            kelvin = round(2700 + cct * 38)
            warmth = "тёплый" if cct < 30 else ("нейтральный" if cct < 70 else "холодный")
            return f"Белый свет, {warmth}, {kelvin} кельвин, яркость {bright} процентов."
    else:
        r, g, b = lamp._last_r, lamp._last_g, lamp._last_b
        if r is None:
            return "Цвет ещё не задавался в этой сессии."
        return f"Цвет RGB: красный {r}, зелёный {g}, синий {b}."


# ─── Вспомогательные функции ──────────────────────────────────────────────────

_LAMP_CMDS = {"on", "off", "brightness", "temp", "rgb", "white", "hsv", "status"}


def _is_hex(cmd: str) -> bool:
    return cmd.startswith("#") or (len(cmd) == 6 and all(c in "0123456789abcdef" for c in cmd))


async def _connect_all_defaults(voice: Voice) -> list[AbstractLampClient]:
    """Подключиться ко всем лампам из DEFAULT_LAMPS.

    Адреса ищутся и подключения выполняются последовательно — BLE-сканирование
    не поддерживает параллельный запуск нескольких find_device.
    Возвращает список успешно подключённых клиентов.
    """
    connected: list[AbstractLampClient] = []
    for entry in DEFAULT_LAMPS:
        addr = entry.get("address", "").strip()
        if not addr:
            addr = await find_lamp(entry["name"], timeout=8.0)
        if not addr:
            continue
        try:
            lamp = await _do_connect(addr, entry["preset"], voice)
            connected.append(lamp)
        except Exception as e:
            print(f"Не удалось подключиться к {entry['name']}: {e}")
    return connected


async def _do_connect(address: str, preset: str, voice: Voice) -> AbstractLampClient:
    """Подключиться к лампе по адресу, сообщить голосом и вернуть клиент."""
    new_lamp = make_lamp(address, preset)
    print(f"Подключение к {address}...", end=" ", flush=True)
    await speak("Подключаюсь.", voice)
    await new_lamp.connect()
    name = getattr(new_lamp, "device_name", address)
    print(f"OK  ({name})")
    await speak(f"Подключено к {name}.", voice)
    return new_lamp


# ─── Выполнение команды ───────────────────────────────────────────────────────

async def execute(
    lamps: list[AbstractLampClient],
    command: str,
    preset: str,
    voice: Voice = None,
) -> list[AbstractLampClient] | None:
    """Разобрать строку-команду и выполнить её.

    Команды рассылаются на все лампы в списке одновременно.
    Возвращает новый список ламп при connect-командах, иначе None.
    """
    parts = command.strip().split()
    if not parts:
        return None

    cmd = parts[0].lower()
    print(f"→ {command}")

    # Команды лампы недоступны без подключения (кроме "on" — он умеет авто-подключаться)
    if (cmd in _LAMP_CMDS or _is_hex(cmd)) and cmd != "on":
        if not lamps:
            print("Лампа не подключена. Скажите 'подключись к <имя>' или запустите с UUID.")
            await speak("Лампа не подключена. Скажите подключись к, и назовите имя устройства.", voice)
            return None

    try:
        if cmd == "on":
            if not lamps:
                # Авто-подключение ко всем дефолтным лампам
                print("Лампа не подключена. Ищу лампы по умолчанию...")
                new_lamps = await _connect_all_defaults(voice)
                if not new_lamps:
                    print("Лампы по умолчанию не найдены.")
                    await speak("Лампа не найдена.", voice)
                    return None
                await asyncio.gather(*[l.turn_on() for l in new_lamps])
                await speak("Включено.", voice)
                print("OK")
                return new_lamps
            await asyncio.gather(*[l.turn_on() for l in lamps])
            await speak("Включено.", voice)
            print("OK")

        elif cmd == "off":
            await asyncio.gather(*[l.turn_off() for l in lamps])
            await speak("Выключено.", voice)
            print("OK")

        elif cmd == "brightness":
            pct = int(parts[1])
            await asyncio.gather(*[l.set_brightness(pct) for l in lamps])
            await speak(f"Яркость {pct} процентов.", voice)
            print("OK")

        elif cmd == "temp":
            k = int(parts[1])
            await asyncio.gather(*[l.set_color_temperature(k) for l in lamps])
            await speak(f"Температура {k} кельвин.", voice)
            print("OK")

        elif cmd == "rgb":
            await asyncio.gather(*[
                l.set_color(int(parts[1]), int(parts[2]), int(parts[3])) for l in lamps
            ])
            await speak("Цвет установлен.", voice)
            print("OK")

        elif cmd == "white":
            surplife = [l for l in lamps if isinstance(l, SurplifeLampClient)]
            if not surplife:
                print("Команда white поддерживается только для Surplife.")
                await speak("Команда белого режима поддерживается только для Surplife.", voice)
                return None
            bright = int(parts[1])
            cct = int(parts[2]) if len(parts) > 2 else 50
            await asyncio.gather(*[l.set_white(bright, cct) for l in surplife])
            await speak(f"Белый режим, яркость {bright} процентов.", voice)
            print("OK")

        elif cmd == "hsv":
            surplife = [l for l in lamps if isinstance(l, SurplifeLampClient)]
            if not surplife:
                print("Команда hsv поддерживается только для Surplife.")
                await speak("Команда HSV поддерживается только для Surplife.", voice)
                return None
            hue = int(parts[1])
            sat = int(parts[2]) if len(parts) > 2 else 100
            bright = int(parts[3]) if len(parts) > 3 else 100
            await asyncio.gather(*[l.set_color_hsv(hue, sat, bright) for l in surplife])
            await speak("Цвет установлен.", voice)
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
            return [await _do_connect(address, preset, voice)]

        elif cmd == "autoconnect":
            name_filter = parts[1] if len(parts) > 1 else None
            print(f"Поиск: {name_filter or 'любую лампу'} (10 сек)...")
            await speak(f"Ищу {'устройство ' + name_filter if name_filter else 'лампу'}.", voice)
            if name_filter:
                address = await find_lamp(name_filter, timeout=10.0)
            else:
                results = await ble_scan(timeout=10.0, name_filter=None)
                named = [d for d, _ in results if d.name]
                address = named[0].address if named else None
            if address is None:
                print("Устройства не найдены.")
                await speak("Устройства поблизости не найдены.", voice)
                return None
            return [await _do_connect(address, preset, voice)]

        elif cmd == "default":
            print("Поиск ламп по умолчанию...")
            await speak("Ищу лампы по умолчанию.", voice)
            new_lamps = await _connect_all_defaults(voice)
            if not new_lamps:
                print("Лампы по умолчанию не найдены.")
                await speak("Лампа не найдена.", voice)
                return None
            return new_lamps

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

        elif cmd == "status":
            status_text = lamp_status_text(lamps[0])
            print(f"Статус: {status_text}")
            await speak(status_text, voice)

        elif _is_hex(cmd):
            r, g, b = parse_hex_color(cmd)
            coros = []
            for lamp in lamps:
                if isinstance(lamp, SurplifeLampClient):
                    h, s, v = rgb_to_hsv(r, g, b)
                    bri = int(parts[1]) if len(parts) > 1 else v
                    coros.append(lamp.set_white(bri) if s == 0 else lamp.set_color_hsv(h, s, bri))
                else:
                    coros.append(lamp.set_color(r, g, b))
            await asyncio.gather(*coros)
            await speak("Цвет установлен.", voice)
            print("OK")

        elif cmd == "speaker":
            action = parts[1].lower() if len(parts) > 1 else ""
            if action == "scan":
                print("Сканирование BT-устройств...")
                await speak("Сканирую, подождите.", voice)
                devices = await speaker.scan(timeout=10.0)
                if not devices:
                    msg = "BT-устройства не найдены."
                else:
                    names = [d["name"] or d["address"] for d in devices]
                    msg = f"Найдено {len(devices)}: {', '.join(names)}."
                    for d in devices:
                        print(f"  {d['address']}  {d['name'] or '(без имени)'}")
                print(msg)
                await speak(msg, voice)
            elif action == "connect":
                name_arg = parts[2] if len(parts) > 2 else ""
                if name_arg:
                    print(f"Поиск BT-устройства «{name_arg}»...")
                    await speak(f"Ищу {name_arg}, подождите.", voice)
                    ok, msg = await speaker.connect_by_name(name_arg)
                elif speaker.is_configured():
                    print("Подключение BT-колонки...")
                    ok, msg = await speaker.connect()
                else:
                    msg = "Укажите имя колонки: «подключись к колонке JBL», или задайте --speaker-mac при запуске."
                    print(msg)
                    await speak(msg, voice)
                    ok = False
                    msg = ""
                if msg:
                    print(f"{'OK' if ok else 'FAIL'}  {msg}")
                    await speak(msg, voice)
            elif action == "disconnect":
                ok, msg = await speaker.disconnect()
                print(f"{'OK' if ok else 'FAIL'}  {msg}")
                await speak(msg, voice)

        else:
            print(f"Неизвестная команда: {cmd!r}")
            await speak("Неизвестная команда.", voice)

    except (IndexError, ValueError) as e:
        print(f"Ошибка параметров: {e}")
        await speak("Ошибка параметров.", voice)
    except Exception as e:
        print(f"Ошибка: {e}")
        await speak("Произошла ошибка.", voice)

    return None
