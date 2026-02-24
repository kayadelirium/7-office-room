# Room Control + Voice Assistant

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

## Ollama в Docker

Вместо нативной установки можно запустить Ollama в контейнере:

```bash
# Запустить (модели хранятся в именованном volume — переживают пересоздание контейнера)
docker compose up -d

# Скачать модель внутрь контейнера
docker exec ollama ollama pull gemma3:4b

# Остановить
docker compose down
```

Ollama будет доступна на `http://127.0.0.1:11434` — адрес по умолчанию для `voice_assistant.py` и `main.py`, настраивать ничего не нужно.

**Linux + NVIDIA GPU** — раскомментировать секцию `deploy` в [docker-compose.yml](docker-compose.yml).

**macOS (Apple Silicon)** — Metal-ускорение в Docker недоступно, модели работают на CPU.

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
| «включи» / «зажги» | включить все лампы (`on`) |
| «выключи» / «потуши» | выключить все лампы (`off`) |
| «включи ленту» / «зажги ленту» | включить только ленту ELK-BLEDOM (`strip on`) |
| «выключи ленту» / «потуши ленту» | выключить только ленту (`strip off`) |
| «сделай ленту красной» | `strip rgb 255 0 0` |
| «яркость ленты 50» | `strip brightness 50` |
| «включи лампу» / «зажги лампу» | включить только настольную лампу Surplife (`lamp on`) |
| «выключи лампу» / «потуши лампу» | выключить только лампу (`lamp off`) |
| «тёплый свет лампы» | `lamp white 70 10` |
| «яркость лампы 80» | `lamp brightness 80` |
| «сделай красный» | `rgb 255 0 0` |
| «синий на 70» | `hsv 240 100 70` |
| «тёплый мягкий свет» | `white 50 10` |
| «ярче» / «сделай потише» | `brightness 80` / `brightness 20` |
| «романтическая атмосфера» | `rgb 180 30 10` |
| «3000 кельвин» | `temp 3000` |
| «какой сейчас цвет» | голосом сообщает текущий режим |
| «включи дефолтную лампу» | найти и подключить все лампы из `DEFAULT_LAMPS` (`ELK-BLEDOM` + `IOTBT5AB`) |
| «подключись к Surplife» | найти и подключить по имени |
| «найди лампы» / «найди BLE устройства» | BLE-сканирование (`scan`) |
| «подключи колонку» / «включи колонку» | подключить BT-колонку (по сохранённому MAC) |
| «подключись к колонке JBL» | найти JBL при сканировании и подключиться |
| «отключи колонку» | отключить BT-колонку, вернуть стандартный аудиовыход |
| «найди колонки» / «поищи наушники» | сканирование BT-колонок (`speaker scan`) |
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
| `--speaker-mac` | `""` | MAC-адрес BT-колонки (`AA:BB:CC:DD:EE:FF`) |
| `--speaker-name` | `""` | Подстрока имени колонки в списке аудиоустройств (`JBL`, `Sony`) |

---

## Ручное управление (CLI)

### Найти адрес лампы

```bash
# BLE-лампы
python main.py --scan

# Классические BT-колонки и наушники
python main.py --scan-speakers
```

На **macOS** адрес лампы выглядит как UUID: `12345678-ABCD-1234-ABCD-1234567890AB`
На **Linux/Windows** — как MAC: `AA:BB:CC:DD:EE:FF`

### Лампы по умолчанию (`--default` / `-d`)

Подключиться ко всем лампам из `DEFAULT_LAMPS` в [voice/commands.py](voice/commands.py) без указания адреса.
Команды рассылаются на все лампы одновременно.

```bash
# Интерактивный режим со всеми лампами по умолчанию
python main.py --default

# Одиночная команда
python main.py --default on
python main.py --default off
python main.py --default brightness 70
```

### Подключиться и управлять

```bash
# Интерактивный режим (конкретная лампа по адресу)
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
python main.py --preset happylighting <MAC>   # ELK-BLEDOM вариант A (сервис FE00)
python main.py --preset elk_bledom <MAC>     # ELK-BLEDOM вариант B / Lotus Lantern (сервис FFF0)
python main.py --preset nus <MAC>
```

---

## Отладка и диагностика

### --inspect: инспекция GATT-сервисов

Подключается к устройству и выводит его полную GATT-иерархию:

```bash
python main.py --inspect <UUID>
```

Пример вывода:

```
[Сервис] 0000fff0-0000-1000-8000-00805f9b34fb  —  Unknown
    [Хар-ка] 0000fff1-0000-1000-8000-00805f9b34fb  props=[read, notify]  —  Unknown
    [Хар-ка] 0000fff3-0000-1000-8000-00805f9b34fb  props=[write-without-response]  —  Unknown
```

**Как читать вывод:**

| Свойство | Значение |
|---|---|
| `read` | можно прочитать значение (`read <uuid>` в интерактивном режиме) |
| `write` / `write-without-response` | сюда отправляются команды управления |
| `notify` / `indicate` | устройство само присылает уведомления об изменении состояния |

**Как определить пресет по UUID характеристики:**

| UUID write-характеристики | Пресет |
|---|---|
| `0000ff01-...` | `surplife` |
| `0000ffd9-...` | `magic_home` |
| `0000ff01-...` (сервис `ffff`) | `triones` |
| `0000ff11-...` (сервис `fe00`) | `happylighting` |
| `0000fff3-...` (сервис `fff0`) | `elk_bledom` (ELK-BLEDOM / Lotus Lantern) |
| `6e400002-b5a3-f393-e0a9-e50e24dcca9e` | `nus` (Nordic UART) |

После определения характеристики используйте соответствующий пресет:

```bash
python main.py --preset magic_home <UUID>
python main.py --preset triones <UUID>
```

Если UUID не совпадает ни с одним известным — можно отправить сырые байты через `write <hex>` в интерактивном режиме и понаблюдать за реакцией устройства.

```bash
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
| `voice/commands.py` | `execute` — разбор и выполнение команд лампы и колонки |
| `speaker/__init__.py` | BT-колонка: `connect`, `connect_by_name`, `disconnect`, `scan` |
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

**macOS** — адрес устройства является CoreBluetooth UUID (не MAC). UUID стабилен для каждой пары хост–устройство, но меняется на другом компьютере. Для управления BT-колонкой (`speaker connect/disconnect/scan`) требуется `blueutil`: `brew install blueutil`.

**Linux** — используется стандартный MAC-адрес. Может потребоваться `sudo` или настройка прав Bluetooth. BT-колонкой управляет `bluetoothctl` (входит в BlueZ). Для записи звука требуется системный пакет: `sudo apt install libportaudio2`.

**Windows** — адрес в формате MAC. BT-подключение и сканирование колонок не поддерживаются; подключите устройство вручную через настройки системы. Переключение аудиовыхода через `--speaker-name` работает.

