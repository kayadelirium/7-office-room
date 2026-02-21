"""
lights/ — контроллеры для световых BLE-устройств.

Текущие контроллеры:
  lamp_client.py      — AbstractLampClient (ABC), BLELampClient,
                        Triones/HappyLighting, конфиги протоколов, утилиты цвета
  surplife_client.py  — SurplifeLampClient (проприетарный протокол Surplife)

Импорт:
  from lights import AbstractLampClient, BLELampClient, SurplifeLampClient
  from lights import make_client, PRESETS, rgb_to_hsv
"""

from .lamp_client import (
    AbstractLampClient,
    BLELampClient,
    LampConfig,
    PRESETS,
    make_client,
    find_lamp,
    parse_hex_color,
    rgb_to_hsv,
    hsv_to_rgb_255,
)
from .surplife_client import SurplifeLampClient

__all__ = [
    "AbstractLampClient",
    "BLELampClient",
    "LampConfig",
    "PRESETS",
    "make_client",
    "find_lamp",
    "parse_hex_color",
    "rgb_to_hsv",
    "hsv_to_rgb_255",
    "SurplifeLampClient",
]
