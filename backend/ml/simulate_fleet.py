"""
RSP COPILOT — сборка цифрового двойника парка энергоблоков для демо.

Для каждого агрегата (гидро-/турбогенератора) на каждой из электростанций
генерируется 180 суток телеметрии (simulate_units.simulate_series), затем:
  1) C++ telemetry_engine прогоняет весь ряд и находит день срабатывания
     классического детектора (абсолютный порог / статистический тренд);
  2) LSTM-модель раннего предупреждения (predictive_model.py) видит только
     первые 90 суток и оценивает вероятность будущей деградации.
Результат — единый fleet.json, который отдаёт FastAPI бэкенд для дашборда
и для RAG-копайлота (последний использует health_status/ai_prob юнита как
контекст при ответе на вопросы диспетчера).
"""
import os
import json
import subprocess

import numpy as np

import sys
sys.path.insert(0, os.path.dirname(__file__))
from simulate_units import simulate_series, PLANTS, N_DAYS, vibration_zone  # noqa: E402
from predictive_model import predict_early_warning  # noqa: E402

HERE = os.path.dirname(__file__)
OUT_DIR = os.path.join(HERE, "..", "data", "fleet")
os.makedirs(OUT_DIR, exist_ok=True)

CPP_ENGINE = os.path.join(HERE, "..", "cpp", "build", "telemetry_engine")

# Число агрегатов на станцию — варьируется, как в реальности (у ГЭС обычно
# больше гидроагрегатов, чем турбогенераторов на блочной ТЭС/АЭС).
UNITS_PER_PLANT = {
    "Богучанская ГЭС": 9,
    "Саяно-Шушенская ГЭС": 10,
    "Балаковская АЭС": 4,
    "Няганьская ГРЭС": 3,
    "Загорская ГАЭС": 6,
    "Волжская ГЭС": 8,
}

RATED_MW = {
    "ГЭС": (155, 333),
    "АЭС": (1000, 1000),
    "ТЭС": (410, 420),
    "ГАЭС": (200, 220),
}


def run_cpp_engine(days, vib, temp):
    lines = "\n".join(f"{d},{v},{t}" for d, v, t in zip(days, vib, temp))
    try:
        proc = subprocess.run([CPP_ENGINE], input=lines, capture_output=True, text=True, timeout=10)
        return json.loads(proc.stdout.strip())
    except Exception as e:  # noqa: BLE001
        return {"status": "UNKNOWN", "error": str(e)}


def build_fleet(seed=7):
    rng_master = np.random.default_rng(seed)
    units = []

    for plant in PLANTS:
        n_units = UNITS_PER_PLANT.get(plant["plant"], 4)
        lo_mw, hi_mw = RATED_MW.get(plant["type"], (200, 300))
        for i in range(1, n_units + 1):
            rng = np.random.default_rng(rng_master.integers(0, 10_000_000))
            unit_id = f"{plant['prefix']}-{i}"
            # риск деградации задаётся заранее для каждого юнита (наработка,
            # условная "история обслуживания") — часть парка исправна, часть
            # имеет развивающийся дефект подшипника разной скорости.
            degrade_risk = float(np.clip(rng.normal(0.28, 0.22), 0.02, 0.9))
            s = simulate_series(rng, degrade_risk=degrade_risk)

            cpp_result = run_cpp_engine(s["days"], s["vibration_mm_s"], s["bearing_temp_c"])
            ai_prob = predict_early_warning(s["vibration_mm_s"], s["bearing_temp_c"])

            abs_day = cpp_result.get("absolute_threshold_day", -1)
            trend_day = cpp_result.get("trend_anomaly_day", -1)
            classic_days = [d for d in (abs_day, trend_day) if d and d > 0]
            classic_detection_day = min(classic_days) if classic_days else None

            lead_time_days = None
            if classic_detection_day is not None and classic_detection_day > 90 and ai_prob is not None and ai_prob >= 0.7:
                lead_time_days = classic_detection_day - 90

            if cpp_result.get("status") == "CRITICAL":
                health_status = "CRITICAL"
            elif cpp_result.get("status") == "WARNING" or (ai_prob is not None and ai_prob >= 0.7):
                health_status = "WARNING"
            else:
                health_status = "OK"

            current_zone = vibration_zone(s["vibration_mm_s"][-1])

            units.append({
                "unit_id": unit_id,
                "plant": plant["plant"],
                "plant_type": plant["type"],
                "rated_capacity_mw": round(float(rng.uniform(lo_mw, hi_mw)), 0),
                "commissioned_year": int(rng.integers(1965, 2019)),
                "telemetry": s,
                "current_vibration_zone": current_zone,
                "cpp_realtime_analysis": cpp_result,
                "ai_early_warning": {
                    "probability": ai_prob,
                    "observed_days": 90,
                },
                "health_status": health_status,
                "classic_detection_day": classic_detection_day,
                "lead_time_days_vs_classic": lead_time_days,
            })
    return units


if __name__ == "__main__":
    fleet = build_fleet()
    with open(os.path.join(OUT_DIR, "fleet.json"), "w", encoding="utf-8") as f:
        json.dump(fleet, f, ensure_ascii=False, indent=2)

    n_degrade = sum(1 for u in fleet if u["telemetry"]["will_degrade"])
    n_critical = sum(1 for u in fleet if u["health_status"] == "CRITICAL")
    n_warning = sum(1 for u in fleet if u["health_status"] == "WARNING")
    leads = [u["lead_time_days_vs_classic"] for u in fleet if u["lead_time_days_vs_classic"]]
    print(f"Парк: {len(fleet)} агрегатов на {len(PLANTS)} станциях")
    print(f"  с реальным сценарием деградации: {n_degrade}")
    print(f"  health_status: CRITICAL={n_critical} WARNING={n_warning} OK={len(fleet)-n_critical-n_warning}")
    if leads:
        print(f"  выигрыш во времени AI vs классика: среднее {sum(leads)/len(leads):.0f} сут, макс {max(leads)} сут (n={len(leads)})")
    else:
        print("  выигрыш во времени AI vs классика: нет случаев в этой выборке")
