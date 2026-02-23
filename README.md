# BLE Lamp Control + Voice Assistant

Управление умными BLE-лампами с Python. Поддерживает лампы **Surplife** (проприетарный протокол) и популярные универсальные протоколы: **Magic Home**, **Govee**, **Triones**, **HappyLighting**, **Nordic UART**.

Включает **голосовой ассистент** на базе Whisper + Ollama/Gemma + Silero TTS — полностью офлайн, без облака.

---

## Установка

```bash
pip install -r requirements.txt
```

Для голосового ассистента дополнительно:

```bash
ollama pull gemma3:4b   # или gemma3:1b для скорости
```

Требуется Python 3.10+.

---

## Голосовой ассистент

### Запуск

```bash
# Без лампы — только голосовое сканирование и подключение
python voice_assistant.py

# Сразу подключиться к лампе
python voice_assistant.py <UUID>

# Whisper с явным русским языком (меньше галлюцинаций, быстрее)
python voice_assistant.py --stt-lang ru

# Другой голос Silero
python voice_assistant.py --silero-speaker xenia

# Быстрый режим (меньше точность, меньше задержка)
python voice_assistant.py --model gemma3:1b --whisper tiny

# Без озвучки
python voice_assistant.py --no-tts
```

### Голосовые команды

| Что сказать | Результат |
|-------------|-----------|
| «включи» / «зажги» | лампа включается |
| «выключи» / «потуши» | лампа выключается |
| «сделай красный» | `rgb 255 0 0` |
| «синий на 70» | `hsv 240 100 70` |
| «тёплый мягкий свет» | `white 50 10` |
| «ярче» / «сделай потише» | `brightness 80` / `brightness 20` |
| «романтическая атмосфера» | `rgb 180 30 10` |
| «3000 кельвин» | `temp 3000` |
| «какой сейчас цвет» | голосом сообщает текущий режим |
| «включи дефолтную лампу» | найти и подключить `IOTBT5AB` |
| «подключись к Surplife» | найти и подключить по имени |
| «найди устройства» | BLE-сканирование |
| всё остальное | разговорный режим (Гемма) |

### Аргументы

| Аргумент | По умолчанию | Описание |
|----------|-------------|----------|
| `--model` / `-m` | `gemma3:4b` | Модель Ollama |
| `--whisper` / `-w` | `small` | Модель Whisper (`tiny`, `base`, `small`, `medium`, `large-v3-turbo`) |
| `--stt-lang` | `""` | Язык распознавания: `ru`, `en` (пусто = авто-определение) |
| `--silero-speaker` | `kseniya` | Русский голос: `xenia`, `aidar`, `baya`, `kseniya`, `eugene` |
| `--silero-en-speaker` | `en_0` | Английский голос: `en_0`…`en_117`, `lj_16khz` |
| `--preset` / `-p` | `surplife` | Протокол лампы |
| `--no-tts` | — | Отключить голосовые ответы |

---

## Ручное управление (CLI)

### Найти адрес лампы

```bash
python main.py --scan
```

На **macOS** адрес выглядит как UUID: `12345678-ABCD-1234-ABCD-1234567890AB`
На **Linux/Windows** — как MAC: `AA:BB:CC:DD:EE:FF`

### Подключиться и управлять

```bash
# Интерактивный режим
python main.py <UUID>

# Одиночная команда и выход
python main.py <UUID> on
python main.py <UUID> off
```

### Команды

```bash
# Питание
python main.py <UUID> on
python main.py <UUID> off

# Цвет (hex)
python main.py <UUID> "#FF8000"         # оранжевый
python main.py <UUID> ff8000            # то же самое
python main.py <UUID> "#FF8000" 70      # оранжевый, яркость 70% (только Surplife)

# Цвет (RGB, 0-255)
python main.py <UUID> rgb 255 0 0
python main.py <UUID> rgb 255 128 0

# Цвет (HSV) — только Surplife
python main.py <UUID> hsv 0   100 80    # красный, яркость 80%
python main.py <UUID> hsv 120 100 80    # зелёный
python main.py <UUID> hsv 240 100 80    # синий

# Белый режим (Surplife): bright 0-100, cct 0=тёплый .. 100=холодный
python main.py <UUID> white 80          # яркость 80%, нейтральный
python main.py <UUID> white 80 0        # тёплый (~2700 K)
python main.py <UUID> white 80 100      # холодный (~6500 K)

# Яркость и температура
python main.py <UUID> brightness 70
python main.py <UUID> temp 4000
```

### Другие протоколы

```bash
python main.py --preset magic_home <MAC>
python main.py --preset govee <MAC>
python main.py --preset triones <MAC>
python main.py --preset happylighting <MAC>
python main.py --preset nus <MAC>
```

---

## Отладка и диагностика

```bash
# Показать GATT-сервисы и характеристики
python main.py --inspect <UUID>

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
| `voice_assistant.py` | Точка входа: аргументы CLI, `voice_loop`, запуск |
| `main.py` | CLI и интерактивный режим управления лампой |
| `scanner.py` | BLE-сканер, инспекция GATT-иерархии |
| `voice/tts.py` | `SileroTTS`, `speak`, `do_and_speak`, определение языка |
| `voice/audio.py` | `VoiceRecorder` — запись с микрофона по VAD |
| `voice/ollama.py` | Промпты, `ask_ollama`, стриминг ответов с TTS |
| `voice/commands.py` | `execute` — разбор и выполнение команд лампы |
| `lights/lamp_client.py` | `AbstractLampClient`, `BLELampClient`, протоколы, утилиты цвета |
| `lights/surplife_client.py` | `SurplifeLampClient` — проприетарный протокол Surplife |
| `requirements.txt` | Зависимости проекта |

---

## Зависимости

| Пакет | Зачем |
|-------|-------|
| [bleak](https://github.com/hbldh/bleak) | BLE-соединение |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Распознавание речи (STT) |
| [sounddevice](https://python-sounddevice.readthedocs.io) | Запись с микрофона |
| numpy | Обработка аудио |
| [torch](https://pytorch.org) + omegaconf + scipy | Silero TTS |
| [Ollama](https://ollama.com) | Локальный LLM (Gemma3) — установить отдельно |

---

## Заметки по платформам

**macOS** — адрес устройства является CoreBluetooth UUID (не MAC). UUID стабилен для каждой пары хост–устройство, но меняется на другом компьютере.

**Linux** — используется стандартный MAC-адрес. Может потребоваться запуск с `sudo` или настройка прав доступа к Bluetooth.

**Windows** — адрес в формате MAC. Требуется Windows 10 версии 1709+ с поддержкой WinRT Bluetooth API.
