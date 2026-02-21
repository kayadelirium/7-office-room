# BLE Lamp Client

Управление умными BLE-лампами с Python. Поддерживает лампы **Surplife** (проприетарный протокол) и популярные универсальные протоколы: **Magic Home**, **Govee**, **Triones**, **HappyLighting**, **Nordic UART**.

---

## Установка

```bash
pip install -r requirements.txt
```

Требуется Python 3.10+.

---

## Быстрый старт

### 1. Найти адрес лампы

```bash
python main.py --scan
```

На **macOS** адрес выглядит как UUID: `12345678-ABCD-1234-ABCD-1234567890AB`
На **Linux/Windows** — как MAC: `AA:BB:CC:DD:EE:FF`

### 2. Подключиться и управлять

```bash
# Интерактивный режим (пресет по умолчанию: surplife)
python main.py <UUID>

# Одиночная команда и выход
python main.py <UUID> on
python main.py <UUID> off
```

---

## Команды

### Питание

```bash
python main.py <UUID> on
python main.py <UUID> off
```

### Белый режим (Surplife)

```bash
python main.py <UUID> white 80          # яркость 80%, нейтральный (~4600 K)
python main.py <UUID> white 80 0        # яркость 80%, тёплый (~2700 K)
python main.py <UUID> white 80 100      # яркость 80%, холодный (~6500 K)
python main.py <UUID> white 80 30       # яркость 80%, слегка тёплый
```

Параметр CCT: `0` = тёплый, `50` = нейтральный, `100` = холодный.

### Цвет

```bash
# Через hex (# необязателен, регистр не важен)
python main.py <UUID> "#FF8000"         # оранжевый
python main.py <UUID> ff8000            # то же самое
python main.py <UUID> "#FF8000" 70      # оранжевый, яркость 70% (только Surplife)

# Через RGB (0-255)
python main.py <UUID> rgb 255 0 0       # красный
python main.py <UUID> rgb 255 128 0     # оранжевый
python main.py <UUID> rgb 0 0 255       # синий

# Через HSV — только Surplife (hue 0-359°, sat 0-100%, bright 0-100%)
python main.py <UUID> hsv 0   100 80    # красный, яркость 80%
python main.py <UUID> hsv 120 100 80    # зелёный, яркость 80%
python main.py <UUID> hsv 240 100 80    # синий,   яркость 80%
```

### Яркость и температура

```bash
python main.py <UUID> brightness 70     # яркость 70%
python main.py <UUID> temp 4000         # цветовая температура 4000 K (2700-6500)
```

---

## Интерактивный режим

Запуск без команды открывает интерактивный режим с подсказками:

```bash
python main.py <UUID>
```

```
Подключено: Surplife_Lamp  (пресет: surplife)

Команды:
  on / off
  #RRGGBB [bright]          hex-цвет (# необязателен)
  rgb <R> <G> <B>
  brightness <0-100>
  temp <2700-6500>
  white <bright> [cct]      (только Surplife)
  hsv <hue> [sat] [bright]  (только Surplife)
  effect <mode_hex> [speed]
  services / read <uuid> / write <hex>
  help / exit

lamp> #FF8000
OK — цвет ███  #FF8000.
lamp> white 70 20
OK — белый ███  70%  CCT=20%.
lamp> rgb 255 128 0
OK — цвет ███  rgb(255, 128, 0).
lamp> exit
```

---

## Другие типы ламп

Выбор протокола через `--preset`:

```bash
# Magic Home / Tuya и большинство китайских RGB-ламп
python main.py --preset magic_home <MAC>

# Govee
python main.py --preset govee <MAC>

# Triones
python main.py --preset triones <MAC>

# HappyLighting / ELK-BLEDOM
python main.py --preset happylighting <MAC>

# Nordic UART Service (DIY на nRF5x)
python main.py --preset nus <MAC>
```

Одиночная команда с пресетом:

```bash
python main.py --preset magic_home <MAC> on
python main.py --preset govee <MAC> rgb 0 255 0
```

---

## Отладка и диагностика

```bash
# Показать GATT-сервисы и характеристики лампы
python main.py --inspect <UUID>

# То же через scanner.py напрямую
python scanner.py --inspect <UUID>

# Подробный лог BLE-операций
python main.py <UUID> --verbose

# Сканирование с фильтром по имени
python main.py --scan --filter "Lamp"
python scanner.py --filter "Surplife"

# Увеличить таймаут сканирования
python main.py --scan --timeout 20
```

---

## Структура проекта

| Файл | Назначение |
|------|------------|
| `main.py` | Единая точка входа: CLI, интерактивный цикл |
| `lamp_client.py` | `AbstractLampClient` (ABC), `BLELampClient`, конфиги протоколов, утилиты цвета |
| `surplife_client.py` | `SurplifeLampClient` — проприетарный протокол Surplife |
| `scanner.py` | BLE-сканер, инспекция GATT-иерархии |
| `requirements.txt` | Зависимость: `bleak` |

---

## Зависимости

- [bleak](https://github.com/hbldh/bleak) — кросс-платформенная библиотека BLE для Python
- Python 3.10+

---

## Заметки по платформам

**macOS** — адрес устройства является CoreBluetooth UUID (не MAC). UUID стабилен для каждой пары хост–устройство, но меняется на другом компьютере. Перед подключением bleak автоматически выполняет сканирование.

**Linux** — используется стандартный MAC-адрес. Может потребоваться запуск с `sudo` или настройка прав доступа к Bluetooth.

**Windows** — адрес в формате MAC. Требуется Windows 10 версии 1709+ с поддержкой WinRT Bluetooth API.
