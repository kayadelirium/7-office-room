"""
main.py — единая точка входа для управления BLE-лампами.

─────────────────────────────────────────────────────────
БЫСТРЫЙ СТАРТ
─────────────────────────────────────────────────────────
Сканировать BLE-лампы поблизости:
    python main.py --scan

Сканировать классические BT-колонки/наушники:
    python main.py --scan-speakers

Посмотреть GATT-сервисы лампы:
    python main.py --inspect <UUID>

Интерактивный режим (Surplife, пресет по умолчанию):
    python main.py <UUID>

Одиночные команды без входа в интерактивный режим:
    python main.py <UUID> on
    python main.py <UUID> off
    python main.py <UUID> white 80          # яркость 80%, нейтральный (CCT 50%)
    python main.py <UUID> white 80 20       # яркость 80%, тёплый (2700 K)
    python main.py <UUID> rgb 255 128 0     # оранжевый через RGB
    python main.py <UUID> hsv 30 100 80     # оранжевый через HSV
    python main.py <UUID> brightness 70
    python main.py <UUID> temp 4000

Другие типы ламп (Magic Home, Govee, Triones, HappyLighting):
    python main.py --preset magic_home <MAC>
    python main.py --preset govee      <MAC> off
    python main.py --preset triones    <MAC>

─────────────────────────────────────────────────────────
ДОСТУПНЫЕ ПРЕСЕТЫ
─────────────────────────────────────────────────────────
  surplife      — лампы Surplife (проприетарный протокол, отдельный seq-заголовок)
  magic_home    — Magic Home, Tuya и большинство китайских RGB-ламп
  govee         — Govee (упрощённый протокол)
  nus           — Nordic UART Service (DIY-лампы на nRF)
  triones       — Triones-совместимые лампы
  happylighting — HappyLighting / ELK-BLEDOM

─────────────────────────────────────────────────────────
АРХИТЕКТУРА
─────────────────────────────────────────────────────────
  lamp_client.py      — AbstractLampClient (ABC), BLELampClient,
                        Triones/HappyLighting клиенты, вспомогательные утилиты,
                        словарь PRESETS с конфигами протоколов
  surplife_client.py  — SurplifeLampClient (наследует AbstractLampClient),
                        реализует проприетарный протокол с seq-заголовком
  scanner.py          — BLE-сканер, инспекция GATT-сервисов
  main.py             — этот файл: CLI-обёртка над всем вышеперечисленным
"""

from __future__ import annotations

import asyncio
import argparse
import logging
import sys

from lamp_client import (
    AbstractLampClient,
    BLELampClient, LampConfig, PRESETS,
    find_lamp, make_client,
    rgb_to_hsv, hsv_to_rgb_255, parse_hex_color,
)
from surplife_client import SurplifeLampClient
from scanner import scan, inspect_device


# ---------------------------------------------------------------------------
# Визуализация цвета в терминале (ANSI 24-bit true-color)
# ---------------------------------------------------------------------------

def _swatch(r: int, g: int, b: int) -> str:
    """
    Цветной прямоугольник в терминале с помощью ANSI escape-кода true-color.

    Формат: ESC[48;2;R;G;Bm   ESC[0m
      48;2  — установить фоновый цвет в RGB
      R G B — компоненты цвета (0-255 каждый)
      m     — завершение ESC-последовательности
      ESC[0m — сброс всех атрибутов (возврат к цвету по умолчанию)

    Работает в большинстве современных терминалов (iTerm2, Windows Terminal,
    GNOME Terminal и т.д.). В старых терминалах выводится просто три пробела.
    """
    return f"\033[48;2;{r};{g};{b}m   \033[0m"


# ---------------------------------------------------------------------------
# Тексты справки
# ---------------------------------------------------------------------------

HELP_COMMON = """\
Команды:
  on                        — включить
  off                       — выключить
  #RRGGBB [bright]          — цвет hex-строкой (# необязателен, напр.: #FF8000 или ff8000)
  rgb <R> <G> <B>           — цвет через RGB (0-255)
  brightness <0-100>        — яркость (%)
  temp <2700-6500>          — цветовая температура (K)
  effect <mode_hex> [speed] — встроенный эффект (0x25-0x38)
  services                  — показать GATT-сервисы
  read <uuid>               — прочитать характеристику
  write <hex>               — отправить raw hex-байты
  help                      — эта справка
  exit / quit               — выйти"""

HELP_SURPLIFE = """\
Дополнительно (только Surplife):
  white <bright> [cct]      — белый режим (cct: 0=тёплый 2700K .. 100=холодный 6500K)
  hsv <hue> [sat] [bright]  — цвет через HSV напрямую (hue 0-359°)"""


# ---------------------------------------------------------------------------
# Интерактивный цикл
# ---------------------------------------------------------------------------

async def interactive_loop(lamp: AbstractLampClient, preset: str) -> None:
    """
    Принимать команды из stdin и выполнять их на лампе.

    Цикл работает до ввода 'exit'/'quit' или нажатия Ctrl+C/Ctrl+D.
    Все ошибки перехватываются и выводятся в консоль — соединение не рвётся.
    """
    # device_name присутствует у SurplifeLampClient (обновляется после connect)
    name = getattr(lamp, "device_name", lamp.address)
    print(f"\nПодключено: {name}  (пресет: {preset})")
    print(HELP_COMMON)
    if isinstance(lamp, SurplifeLampClient):
        print(HELP_SURPLIFE)

    loop = asyncio.get_event_loop()

    while True:
        try:
            # run_in_executor позволяет вызвать блокирующий input() в asyncio-цикле
            line = await loop.run_in_executor(None, lambda: input("lamp> ").strip())
        except (EOFError, KeyboardInterrupt):
            print("\nВыход.")
            break

        if not line:
            continue

        parts = line.split()
        cmd = parts[0].lower()

        try:
            if cmd in ("exit", "quit"):
                break

            elif cmd == "on":
                await lamp.turn_on()
                print("OK — включено.")

            elif cmd == "off":
                await lamp.turn_off()
                print("OK — выключено.")

            elif cmd == "rgb":
                if len(parts) < 4:
                    print("Использование: rgb <R> <G> <B>")
                    continue
                r, g, b = int(parts[1]), int(parts[2]), int(parts[3])
                await lamp.set_color(r, g, b)
                print(f"OK — цвет {_swatch(r, g, b)}  rgb({r}, {g}, {b}).")

            elif cmd == "brightness":
                if len(parts) < 2:
                    print("Использование: brightness <0-100>")
                    continue
                pct = int(parts[1])
                await lamp.set_brightness(pct)
                print(f"OK — яркость {pct}%.")

            elif cmd == "temp":
                if len(parts) < 2:
                    print("Использование: temp <2700-6500>")
                    continue
                k = int(parts[1])
                await lamp.set_color_temperature(k)
                print(f"OK — температура {k} K.")

            elif cmd == "effect":
                if len(parts) < 2:
                    print("Использование: effect <mode_hex> [speed]")
                    continue
                mode = int(parts[1], 0)
                speed = int(parts[2], 0) if len(parts) > 2 else 0x50
                await lamp.set_effect(mode, speed)
                print(f"OK — эффект 0x{mode:02x}.")

            elif cmd == "services":
                await lamp.dump_services()

            elif cmd == "read":
                if len(parts) < 2:
                    print("Использование: read <uuid>")
                    continue
                data = await lamp.read_characteristic(parts[1])
                print(f"← {data.hex()}  ({list(data)})")

            elif cmd == "write":
                if len(parts) < 2:
                    print("Использование: write <hex>  (напр.: write 7123ff)")
                    continue
                raw = bytes.fromhex(parts[1])
                await lamp.write_raw(raw)
                print(f"OK — отправлено {raw.hex()}.")

            # ── Surplife-специфичные команды ──────────────────────────────

            elif cmd == "white":
                if not isinstance(lamp, SurplifeLampClient):
                    print("Команда 'white' доступна только для Surplife.")
                    continue
                if len(parts) < 2:
                    print("Использование: white <bright> [cct]")
                    continue
                bright = int(parts[1])
                cct = int(parts[2]) if len(parts) >= 3 else 50
                await lamp.set_white(bright, cct)
                g_val = int(bright * 255 / 100)
                print(f"OK — белый {_swatch(g_val, g_val, g_val)}  {bright}%  CCT={cct}%.")

            elif cmd == "hsv":
                if not isinstance(lamp, SurplifeLampClient):
                    print("Команда 'hsv' доступна только для Surplife.")
                    continue
                if len(parts) < 2:
                    print("Использование: hsv <hue> [sat] [bright]")
                    continue
                hue = int(parts[1])
                sat = int(parts[2]) if len(parts) >= 3 else 100
                bright = int(parts[3]) if len(parts) >= 4 else 100
                await lamp.set_color_hsv(hue, sat, bright)
                r, g, b = hsv_to_rgb_255(hue, sat, bright)
                print(f"OK — HSV {_swatch(r, g, b)}  hue={hue}°  sat={sat}%  bright={bright}%.")

            elif cmd.startswith("#") or (len(cmd) == 6 and all(c in "0123456789abcdef" for c in cmd)):
                # Hex-цвет: #RRGGBB или RRGGBB, опционально с яркостью (только Surplife)
                r, g, b = parse_hex_color(cmd)
                bri = int(parts[1]) if len(parts) >= 2 else None
                h, s, v = rgb_to_hsv(r, g, b)
                actual_bri = bri if bri is not None else v
                if isinstance(lamp, SurplifeLampClient):
                    if s == 0:
                        await lamp.set_white(actual_bri)
                    else:
                        await lamp.set_color_hsv(h, s, actual_bri)
                else:
                    await lamp.set_color(r, g, b)
                print(f"OK — цвет {_swatch(r, g, b)}  #{r:02X}{g:02X}{b:02X}.")

            elif cmd == "help":
                print(HELP_COMMON)
                if isinstance(lamp, SurplifeLampClient):
                    print(HELP_SURPLIFE)

            else:
                print(f"Неизвестная команда: {cmd!r}. Введите 'help'.")

        except NotImplementedError as exc:
            print(f"Не поддерживается: {exc}")
        except Exception as exc:
            print(f"Ошибка: {exc}")


# ---------------------------------------------------------------------------
# Одиночная команда (неинтерактивный режим)
# ---------------------------------------------------------------------------

async def run_command(lamp: AbstractLampClient, cmd_args: list[str]) -> None:
    """
    Выполнить команду, переданную аргументами командной строки, и вернуться.

    cmd_args — список строк, первый элемент — имя команды, остальные — параметры.
    Примеры:
        ["on"]
        ["off"]
        ["white", "80", "30"]
        ["rgb", "255", "128", "0"]
        ["hsv", "120", "100", "80"]
        ["brightness", "70"]
        ["temp", "4000"]
        ["#FF8000"]           — hex-цвет
    """
    if not cmd_args:
        return

    cmd = cmd_args[0].lower()

    if cmd == "on":
        await lamp.turn_on()
        print("OK — включено.")

    elif cmd == "off":
        await lamp.turn_off()
        print("OK — выключено.")

    elif cmd == "brightness":
        pct = int(cmd_args[1])
        await lamp.set_brightness(pct)
        print(f"OK — яркость {pct}%.")

    elif cmd == "temp":
        k = int(cmd_args[1])
        await lamp.set_color_temperature(k)
        print(f"OK — температура {k} K.")

    elif cmd == "rgb":
        r, g, b = int(cmd_args[1]), int(cmd_args[2]), int(cmd_args[3])
        await lamp.set_color(r, g, b)
        print(f"OK — цвет {_swatch(r, g, b)}  rgb({r}, {g}, {b}).")

    elif cmd == "white":
        if not isinstance(lamp, SurplifeLampClient):
            print("Команда 'white' доступна только для Surplife.")
            sys.exit(1)
        bright = int(cmd_args[1])
        cct = int(cmd_args[2]) if len(cmd_args) >= 3 else 50
        await lamp.set_white(bright, cct)
        g_val = int(bright * 255 / 100)
        print(f"OK — белый {_swatch(g_val, g_val, g_val)}  {bright}%  CCT={cct}%.")

    elif cmd == "hsv":
        if not isinstance(lamp, SurplifeLampClient):
            print("Команда 'hsv' доступна только для Surplife.")
            sys.exit(1)
        hue = int(cmd_args[1])
        sat = int(cmd_args[2]) if len(cmd_args) >= 3 else 100
        bright = int(cmd_args[3]) if len(cmd_args) >= 4 else 100
        await lamp.set_color_hsv(hue, sat, bright)
        r, g, b = hsv_to_rgb_255(hue, sat, bright)
        print(f"OK — HSV {_swatch(r, g, b)}  hue={hue}°  sat={sat}%  bright={bright}%.")

    elif cmd.startswith("#") or (len(cmd) == 6 and all(c in "0123456789abcdefABCDEF" for c in cmd)):
        # Hex-цвет: #RRGGBB или RRGGBB, опционально + яркость
        r, g, b = parse_hex_color(cmd)
        bri = int(cmd_args[1]) if len(cmd_args) >= 2 else None
        h, s, v = rgb_to_hsv(r, g, b)
        actual_bri = bri if bri is not None else v
        if isinstance(lamp, SurplifeLampClient):
            if s == 0:
                await lamp.set_white(actual_bri)
            else:
                await lamp.set_color_hsv(h, s, actual_bri)
        else:
            await lamp.set_color(r, g, b)
        print(f"OK — цвет {_swatch(r, g, b)}  #{r:02X}{g:02X}{b:02X}.")

    else:
        print(f"Неизвестная команда: {cmd!r}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Фабрика клиентов
# ---------------------------------------------------------------------------

def _make_lamp(address: str, preset: str) -> AbstractLampClient:
    """
    Создать клиент нужного типа по имени пресета.

    Surplife использует собственный класс (surplife_client.py).
    Все остальные пресеты обрабатываются универсальным BLELampClient
    (или его специализированными подклассами — TrionesLampClient и др.)
    через фабричную функцию make_client().
    """
    if preset == "surplife":
        return SurplifeLampClient(address)

    # Callback для входящих BLE-уведомлений — печатаем hex и возвращаем управление
    def notify_cb(data: bytes) -> None:
        print(f"\n← уведомление: {data.hex()}  ({list(data)})\nlamp> ",
              end="", flush=True)

    return make_client(address, preset, on_notify=notify_cb)


# ---------------------------------------------------------------------------
# Главная async-функция
# ---------------------------------------------------------------------------

async def async_main(args: argparse.Namespace) -> None:
    # ── Режим сканирования BLE ────────────────────────────────────────────
    if args.scan:
        await scan(timeout=args.timeout, name_filter=args.filter)
        return

    # ── Режим сканирования классического BT (колонки, наушники) ──────────
    if args.scan_speakers:
        from speaker import scan as bt_scan
        print(f"Сканирование BT-устройств ({args.timeout:.0f} сек)...")
        devices = await bt_scan(timeout=args.timeout)
        if not devices:
            print("BT-устройства не найдены.")
        else:
            print(f"Найдено {len(devices)} устройств:")
            for d in devices:
                name = d.get("name") or "(без имени)"
                addr = d.get("address", "")
                print(f"  {addr}  {name}")
        return

    # ── Режим инспекции GATT ──────────────────────────────────────────────
    if args.inspect:
        await inspect_device(args.inspect)
        return

    # ── Нужен адрес устройства ────────────────────────────────────────────
    if not args.address:
        print("Укажите UUID/MAC устройства или используйте --scan для поиска.")
        sys.exit(1)

    lamp = _make_lamp(args.address, args.preset)

    # ── Подключение и выполнение ──────────────────────────────────────────
    if isinstance(lamp, SurplifeLampClient):
        # SurplifeLampClient не является context manager с автоотключением
        # в bleak-стиле, поэтому управляем соединением вручную
        print(f"Подключение к {args.address}…", end=" ", flush=True)
        await lamp.connect()
        print(f"OK  ({lamp.device_name})")
        try:
            if args.cmd:
                await run_command(lamp, args.cmd)
            else:
                await interactive_loop(lamp, args.preset)
        finally:
            await lamp.disconnect()
    else:
        # BLELampClient поддерживает async with (через AbstractLampClient.__aenter__)
        async with lamp:
            if args.cmd:
                await run_command(lamp, args.cmd)
            else:
                await interactive_loop(lamp, args.preset)


# ---------------------------------------------------------------------------
# Разбор аргументов командной строки
# ---------------------------------------------------------------------------

def main() -> None:
    valid_presets = list(PRESETS.keys()) + ["surplife"]

    parser = argparse.ArgumentParser(
        description="BLE Lamp Client — управление умной лампой через BLE",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Специальные режимы (взаимоисключающие)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--scan", "-s", action="store_true",
                      help="Сканировать BLE-устройства поблизости")
    mode.add_argument("--scan-speakers", action="store_true",
                      help="Сканировать классические BT-устройства (колонки, наушники)")
    mode.add_argument("--inspect", metavar="UUID",
                      help="Подключиться к устройству и показать его GATT-сервисы")

    # UUID лампы — первый позиционный аргумент (необязателен при --scan/--inspect)
    parser.add_argument("address", nargs="?",
                        metavar="UUID",
                        help="MAC / UUID адрес лампы")

    # Команда — все оставшиеся позиционные аргументы
    # Примеры: on | off | white 80 30 | rgb 255 128 0 | hsv 30 100 80
    parser.add_argument("cmd", nargs="*",
                        metavar="CMD",
                        help="Команда (on/off/white/rgb/hsv/brightness/temp/...)")

    # Пресет протокола
    parser.add_argument("--preset", "-p", default="surplife",
                        choices=valid_presets,
                        help=f"Пресет протокола (по умолчанию: surplife). "
                             f"Доступны: {', '.join(valid_presets)}")

    # Дополнительные опции
    parser.add_argument("--timeout", "-t", type=float, default=10.0,
                        help="Таймаут сканирования в секундах (default: 10)")
    parser.add_argument("--filter", "-f",
                        help="Фильтр по имени при --scan")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Подробный лог (уровень DEBUG)")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
