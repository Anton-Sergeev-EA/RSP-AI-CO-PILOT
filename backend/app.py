"""
RSP AI Co-Pilot — FastAPI backend.

Собирает воедино: LSTM раннего предупреждения (ml/predictive_model.py),
потоковый C++ движок телеметрии (cpp/build/telemetry_engine), чат-копайлот
диспетчера на базе RAG (copilot.py) и отдаёт всё дашборду (frontend/) как
единый цифровой двойник парка агрегатов, встроенный в SCADA/HMI-подобный
интерфейс.
"""
import os
import sys
import json
import subprocess

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from copilot import ask as copilot_ask  # noqa: E402

FLEET_PATH = os.path.join(HERE, "data", "fleet", "fleet.json")
CPP_ENGINE = os.path.join(HERE, "cpp", "build", "telemetry_engine")
FRONTEND_DIR = os.path.join(HERE, "..", "frontend")

app = FastAPI(title="RSP AI Co-Pilot")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def load_fleet():
    with open(FLEET_PATH, encoding="utf-8") as f:
        return json.load(f)


def find_unit(fleet, unit_id, plant=None):
    # unit_id сам по себе не уникален: одинаковые обозначения (ГА-1, ГА-2, ...)
    # встречаются на разных станциях, поэтому при наличии plant им и уточняем.
    candidates = [u for u in fleet if u["unit_id"] == unit_id]
    if plant:
        exact = [u for u in candidates if u["plant"] == plant]
        if exact:
            return exact[0]
    return candidates[0] if candidates else None


@app.get("/api/plants")
def list_plants():
    fleet = load_fleet()
    plants = {}
    for u in fleet:
        p = plants.setdefault(u["plant"], {
            "plant": u["plant"], "plant_type": u["plant_type"],
            "count": 0, "critical": 0, "warning": 0, "ok": 0,
        })
        p["count"] += 1
        p[u["health_status"].lower()] += 1
    return {"plants": list(plants.values())}


@app.get("/api/units")
def list_units():
    fleet = load_fleet()
    summary = [{
        "unit_id": u["unit_id"],
        "plant": u["plant"],
        "plant_type": u["plant_type"],
        "rated_capacity_mw": u["rated_capacity_mw"],
        "current_vibration_zone": u["current_vibration_zone"],
        "health_status": u["health_status"],
        "ai_probability": u["ai_early_warning"]["probability"],
        "lead_time_days_vs_classic": u["lead_time_days_vs_classic"],
    } for u in fleet]
    return {
        "count": len(summary),
        "critical": sum(1 for s in summary if s["health_status"] == "CRITICAL"),
        "warning": sum(1 for s in summary if s["health_status"] == "WARNING"),
        "ok": sum(1 for s in summary if s["health_status"] == "OK"),
        "units": summary,
    }


@app.get("/api/units/{unit_id}")
def get_unit(unit_id: str, plant: str | None = None):
    fleet = load_fleet()
    unit = find_unit(fleet, unit_id, plant)
    if not unit:
        raise HTTPException(status_code=404, detail="unit not found")
    return unit


@app.get("/api/units/{unit_id}/realtime-replay")
def realtime_replay(unit_id: str, up_to_day: int = 180, plant: str | None = None):
    """Прогоняет телеметрию юнита через C++ движок только до дня up_to_day —
    имитация поступления данных потоком для демонстрации работы движка."""
    fleet = load_fleet()
    unit = find_unit(fleet, unit_id, plant)
    if not unit:
        raise HTTPException(status_code=404, detail="unit not found")
    t = unit["telemetry"]
    days = t["days"][:up_to_day]
    vib = t["vibration_mm_s"][:up_to_day]
    temp = t["bearing_temp_c"][:up_to_day]
    lines = "\n".join(f"{d},{v},{te}" for d, v, te in zip(days, vib, temp))
    try:
        proc = subprocess.run([CPP_ENGINE], input=lines, capture_output=True, text=True, timeout=10)
        result = json.loads(proc.stdout.strip())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))
    return result


@app.get("/api/engine/benchmark")
def engine_benchmark(n: int = 1_000_000):
    n = min(max(n, 1000), 20_000_000)
    try:
        proc = subprocess.run([CPP_ENGINE, "--bench", str(n)], capture_output=True, text=True, timeout=30)
        return json.loads(proc.stdout.strip())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))


class CopilotQuestion(BaseModel):
    question: str


@app.post("/api/copilot/ask")
def copilot_endpoint(payload: CopilotQuestion):
    q = (payload.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="empty question")
    return copilot_ask(q)


@app.get("/api/fleet/stats")
def fleet_stats():
    fleet = load_fleet()
    leads = [u["lead_time_days_vs_classic"] for u in fleet if u["lead_time_days_vs_classic"]]
    return {
        "total_units": len(fleet),
        "degrading_units": sum(1 for u in fleet if u["telemetry"]["will_degrade"]),
        "critical": sum(1 for u in fleet if u["health_status"] == "CRITICAL"),
        "warning": sum(1 for u in fleet if u["health_status"] == "WARNING"),
        "avg_lead_time_days": round(sum(leads) / len(leads), 1) if leads else None,
        "max_lead_time_days": max(leads) if leads else None,
        "cases_with_lead_time": len(leads),
    }


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "cpp_engine_present": os.path.exists(CPP_ENGINE),
        "fleet_size": len(load_fleet()),
    }


# Статика фронтенда — раздаём собранный дашборд той же FastAPI-инстанцией,
# чтобы всё демо поднималось одной командой.
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
