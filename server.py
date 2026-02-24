"""server.py — HTTP-сервер управления лампами и колонкой.

Запуск:
    python server.py                   # http://0.0.0.0:8765
    python server.py --port 9000       # другой порт
    python server.py --host 127.0.0.1  # только localhost

Требует:
    pip install fastapi uvicorn
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from lights import AbstractLampClient, make_client
from lights.surplife_client import SurplifeLampClient
from scanner import scan as ble_scan
import speaker


# ─── Глобальное состояние ─────────────────────────────────────────────────────

_lamps: list[AbstractLampClient] = []


def _lamp_dict(lamp: AbstractLampClient) -> dict:
    return {
        "address": lamp.address,
        "name":    getattr(lamp, "device_name", "") or lamp.address,
        "type":    "lamp" if isinstance(lamp, SurplifeLampClient) else "strip",
    }


def _targets(target: str) -> list[AbstractLampClient]:
    match target:
        case "all":   return list(_lamps)
        case "strip": return [l for l in _lamps if not isinstance(l, SurplifeLampClient)]
        case "lamp":  return [l for l in _lamps if isinstance(l, SurplifeLampClient)]
        case _:       return [l for l in _lamps if l.address == target]


# ─── FastAPI ───────────────────────────────────────────────────────────────────

_static = Path(__file__).parent / "static"

app = FastAPI(title="Room Control")
app.mount("/static", StaticFiles(directory=str(_static)), name="static")


@app.get("/")
async def index():
    return FileResponse(_static / "index.html")


# ── State ─────────────────────────────────────────────────────────────────────

@app.get("/api/state")
async def get_state():
    return {
        "lamps":              [_lamp_dict(l) for l in _lamps],
        "speaker_configured": speaker.is_configured(),
    }


# ── BLE Scan ──────────────────────────────────────────────────────────────────

@app.post("/api/scan")
async def scan_ble(timeout: float = 8.0):
    results = await ble_scan(timeout=timeout, name_filter=None)
    seen, out = set(), []
    for dev, adv in results:
        if dev.address not in seen:
            seen.add(dev.address)
            out.append({
                "address": dev.address,
                "name":    dev.name or "",
                "rssi":    adv.rssi,
            })
    return sorted(out, key=lambda d: -d["rssi"])


# ── Connect / Disconnect ──────────────────────────────────────────────────────

class ConnectBody(BaseModel):
    address: str
    preset: str = "surplife"


@app.post("/api/connect")
async def connect(body: ConnectBody):
    if any(l.address == body.address for l in _lamps):
        return {"ok": False, "error": "Уже подключено"}
    try:
        lamp = (SurplifeLampClient(body.address)
                if body.preset == "surplife"
                else make_client(body.address, body.preset))
        await lamp.connect()
        _lamps.append(lamp)
        return {"ok": True, "lamp": _lamp_dict(lamp)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class DisconnectBody(BaseModel):
    address: str


@app.post("/api/disconnect")
async def disconnect(body: DisconnectBody):
    for i, lamp in enumerate(_lamps):
        if lamp.address == body.address:
            try:
                await lamp.disconnect()
            except Exception:
                pass
            _lamps.pop(i)
            return {"ok": True}
    return {"ok": False, "error": "Не найдено"}


# ── Commands ──────────────────────────────────────────────────────────────────

class CmdBody(BaseModel):
    target: str = "all"   # "all" | "strip" | "lamp" | <address>
    cmd: str               # "on" | "off" | "brightness 70" | "rgb 255 0 0" | …


@app.post("/api/command")
async def command(body: CmdBody):
    targets = _targets(body.target)
    if not targets:
        return {"ok": False, "error": "Нет устройств"}
    parts = body.cmd.strip().split()
    if not parts:
        return {"ok": False, "error": "Пустая команда"}

    cmd = parts[0].lower()
    args = parts[1:]
    try:
        match cmd:
            case "on":
                await asyncio.gather(*[l.turn_on() for l in targets])
            case "off":
                await asyncio.gather(*[l.turn_off() for l in targets])
            case "brightness":
                pct = int(args[0])
                await asyncio.gather(*[l.set_brightness(pct) for l in targets])
            case "rgb":
                r, g, b = int(args[0]), int(args[1]), int(args[2])
                await asyncio.gather(*[l.set_color(r, g, b) for l in targets])
            case "white":
                sl = [l for l in targets if isinstance(l, SurplifeLampClient)]
                bright = int(args[0])
                cct = int(args[1]) if len(args) > 1 else 50
                await asyncio.gather(*[l.set_white(bright, cct) for l in sl])
            case "hsv":
                sl = [l for l in targets if isinstance(l, SurplifeLampClient)]
                h = int(args[0])
                s = int(args[1]) if len(args) > 1 else 100
                v = int(args[2]) if len(args) > 2 else 100
                await asyncio.gather(*[l.set_color_hsv(h, s, v) for l in sl])
            case "temp":
                k = int(args[0])
                await asyncio.gather(*[l.set_color_temperature(k) for l in targets])
            case _:
                return {"ok": False, "error": f"Неизвестная команда: {cmd!r}"}
        return {"ok": True}
    except (IndexError, ValueError) as e:
        return {"ok": False, "error": f"Параметры: {e}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Speaker ───────────────────────────────────────────────────────────────────

@app.post("/api/speaker/scan")
async def sp_scan():
    return await speaker.scan(timeout=8.0)


class SpConnectBody(BaseModel):
    name: str = ""


@app.post("/api/speaker/connect")
async def sp_connect(body: SpConnectBody):
    if body.name:
        ok, msg = await speaker.connect_by_name(body.name)
    else:
        ok, msg = await speaker.connect()
    return {"ok": ok, "message": msg}


@app.post("/api/speaker/disconnect")
async def sp_disconnect():
    ok, msg = await speaker.disconnect()
    return {"ok": ok, "message": msg}


# ─── Запуск ───────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="BLE Lamp Control — HTTP-сервер")
    ap.add_argument("--host", default="0.0.0.0",
                    help="Хост (по умолчанию: 0.0.0.0)")
    ap.add_argument("--port", type=int, default=8765,
                    help="Порт (по умолчанию: 8765)")
    args = ap.parse_args()
    print(f"Открыть: http://localhost:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
