"""
scanner.py — поиск BLE-устройств и инспекция GATT-сервисов.

─────────────────────────────────────────────────────────
ИСПОЛЬЗОВАНИЕ КАК СКРИПТ
─────────────────────────────────────────────────────────
Сканировать все BLE-устройства поблизости (10 сек):
    python scanner.py

С фильтром по имени:
    python scanner.py --filter "Lamp"

С другим таймаутом:
    python scanner.py --timeout 20

Посмотреть GATT-сервисы конкретного устройства:
    python scanner.py --inspect <UUID>

─────────────────────────────────────────────────────────
ИСПОЛЬЗОВАНИЕ ИЗ main.py
─────────────────────────────────────────────────────────
    python main.py --scan
    python main.py --inspect <UUID>

─────────────────────────────────────────────────────────
ЧТО ТАКОЕ BLE-СКАНИРОВАНИЕ
─────────────────────────────────────────────────────────
BLE-устройства периодически рассылают пакеты Advertisement (маяки),
содержащие имя устройства, UUID поддерживаемых сервисов, RSSI (мощность
сигнала) и данные производителя. Сканирование — пассивный приём этих пакетов.

На macOS Bluetooth-адрес заменён на CoreBluetooth UUID (случайный GUID,
стабильный для каждой пары хост–устройство). Именно этот UUID нужно передавать
в SurplifeLampClient и BLELampClient.

─────────────────────────────────────────────────────────
ЧТО ТАКОЕ GATT-ИНСПЕКЦИЯ
─────────────────────────────────────────────────────────
GATT (Generic Attribute Profile) — иерархическая структура BLE-устройства:
  Устройство
  └── Сервис (Service)          — UUID, группирует функциональность
      └── Характеристика (Char) — UUID, свойства (read/write/notify)
          └── Дескриптор        — UUID, описание характеристики

Инспекция помогает понять протокол незнакомой лампы:
  1. Найдите write-характеристику (props содержит "write").
  2. Найдите notify-характеристику (props содержит "notify").
  3. Используйте эти UUID в --service-uuid / --write-uuid или добавьте пресет.
"""

from __future__ import annotations

import asyncio
import argparse
from bleak import BleakScanner, BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData


# ---------------------------------------------------------------------------
# Форматирование информации об устройстве
# ---------------------------------------------------------------------------

def format_device(device: BLEDevice, adv: AdvertisementData) -> str:
    """
    Сформировать читаемую строку с информацией о BLE-устройстве.

    Поля:
      Имя      — advertised local name (или "<без имени>")
      Адрес    — MAC (Linux/Windows) или CoreBluetooth UUID (macOS)
      RSSI     — Received Signal Strength Indicator в dBm
                 (типично: -30...-60 dBm хорошо, -80...-90 плохо)
      UUID-сервисы — список UUID сервисов из Advertisement пакетов
      Производитель — данные производителя (Manufacturer Specific Data):
                      ключ = company code (Bluetooth SIG), значение = hex
    """
    name = device.name or "<без имени>"
    rssi = adv.rssi
    uuids = adv.service_uuids
    mfr = adv.manufacturer_data

    lines = [
        f"  Имя    : {name}",
        f"  Адрес  : {device.address}",
        f"  RSSI   : {rssi} dBm",
    ]
    if uuids:
        lines.append(f"  UUID-сервисы: {', '.join(uuids)}")
    if mfr:
        for code, data in mfr.items():
            lines.append(f"  Производитель [{code:#06x}]: {data.hex()}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Активное сканирование
# ---------------------------------------------------------------------------

async def scan(
    timeout: float,
    name_filter: str | None,
) -> list[tuple[BLEDevice, AdvertisementData]]:
    """
    Сканировать BLE-устройства в течение заданного времени.

    timeout      — время сканирования в секундах.
    name_filter  — если задан, показывать только устройства с этой
                   подстрокой в имени (регистронезависимо).

    Возвращает список найденных пар (BLEDevice, AdvertisementData).

    Механизм:
      BleakScanner используется как context manager — при входе начинает
      сканирование, при выходе останавливает. detection_callback вызывается
      при каждом новом Advertisement-пакете (одно устройство может
      появиться несколько раз — bleak не дедуплицирует автоматически).
    """
    print(f"Сканирование BLE-устройств ({timeout} сек)...\n")
    found: list[tuple[BLEDevice, AdvertisementData]] = []

    def callback(device: BLEDevice, adv: AdvertisementData) -> None:
        """Callback, вызываемый при обнаружении каждого устройства."""
        if name_filter:
            # Пропустить устройства без имени или с несовпадающим именем
            if not device.name or name_filter.lower() not in device.name.lower():
                return
        found.append((device, adv))
        print(f"[{len(found):>3}] Найдено:")
        print(format_device(device, adv))
        print()

    async with BleakScanner(detection_callback=callback):
        await asyncio.sleep(timeout)

    print(f"Итого найдено: {len(found)} устройств.")
    return found


# ---------------------------------------------------------------------------
# Инспекция GATT-сервисов
# ---------------------------------------------------------------------------

async def inspect_device(address: str, timeout: float = 10.0) -> None:
    """
    Подключиться к устройству и вывести его полную GATT-иерархию.

    address — MAC (Linux/Windows) или CoreBluetooth UUID (macOS).
              Получить адрес можно через scan() или python main.py --scan.

    Выводит:
      [Сервис] <uuid> — <description>
          [Хар-ка] <uuid>  props=[read, write, notify, ...]  — <description>
              [Дескриптор] <uuid> — <description>

    Свойства характеристики (props):
      read   — можно прочитать значение (read_gatt_char)
      write  — можно записать без подтверждения (write_gatt_char, response=False)
      write-without-response — то же самое (некоторые прошивки называют иначе)
      notify — лампа сама отправляет уведомления при изменении состояния
      indicate — то же что notify, но с подтверждением от хоста
    """
    # На macOS требуется предварительное сканирование перед подключением
    print(f"Поиск устройства {address} ({timeout} сек)...")
    device = await BleakScanner.find_device_by_address(address, timeout=timeout)
    if device is None:
        print(
            f"Устройство {address} не найдено.\n"
            "Убедитесь что лампа включена и находится рядом,\n"
            "затем запустите: python main.py --scan  чтобы увидеть правильный адрес."
        )
        return

    print(f"Найдено: {device.name}  [{device.address}]\nПодключение...\n")
    async with BleakClient(device) as client:
        print(f"Подключено: {client.is_connected}\n")
        for service in client.services:
            print(f"[Сервис] {service.uuid}  —  {service.description}")
            for char in service.characteristics:
                props = ", ".join(char.properties)
                print(f"    [Хар-ка] {char.uuid}  props=[{props}]  —  {char.description}")
                for desc in char.descriptors:
                    print(f"        [Дескриптор] {desc.uuid}  —  {desc.description}")
            print()


# ---------------------------------------------------------------------------
# CLI (запуск напрямую: python scanner.py ...)
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="BLE Device Scanner — поиск и инспекция BLE-устройств",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--timeout", type=float, default=10.0,
                        help="Время сканирования в секундах (default: 10)")
    parser.add_argument("--filter", type=str, default=None,
                        metavar="NAME",
                        help="Показать только устройства с этой подстрокой в имени")
    parser.add_argument("--inspect", type=str, default=None,
                        metavar="UUID",
                        help="Подключиться к устройству и показать его GATT-сервисы")
    args = parser.parse_args()

    if args.inspect:
        asyncio.run(inspect_device(args.inspect))
    else:
        asyncio.run(scan(args.timeout, args.filter))


if __name__ == "__main__":
    main()
