"""
RSP COPILOT — симуляция телеметрии энергоблоков (гидро-/турбоагрегатов).

Ключевые параметры мониторинга вращающегося оборудования: виброскорость
подшипников (мм/с, СКЗ — по аналогии с зонами оценки ISO 10816, упрощённо
для демонстрации) и температура подшипников (°C). Для каждого юнита
симулируется 180 суток эксплуатации; часть юнитов получает скрытый
"предвестник" деградации (износ подшипника), который слабо проявляется
с первых же суток и нарастает — ровно та задача, которую должна решать
модель раннего предупреждения.
"""
import numpy as np

N_DAYS = 180
OBS_WINDOW = 90  # окно наблюдения для модели раннего предупреждения

PLANTS = [
    {"plant": "Богучанская ГЭС", "type": "ГЭС", "prefix": "ГА"},
    {"plant": "Саяно-Шушенская ГЭС", "type": "ГЭС", "prefix": "ГА"},
    {"plant": "Балаковская АЭС", "type": "АЭС", "prefix": "ТГ"},
    {"plant": "Няганьская ГРЭС", "type": "ТЭС", "prefix": "ТГ"},
    {"plant": "Загорская ГАЭС", "type": "ГАЭС", "prefix": "ГА"},
    {"plant": "Волжская ГЭС", "type": "ГЭС", "prefix": "ГА"},
]

# Упрощённые (иллюстративные) зоны виброскорости по аналогии с логикой ISO 10816
# для крупных вращающихся машин: A — хорошее состояние, B — приемлемое,
# C — требует внимания, D — недопустимо. Точные границы зависят от класса
# машины и должны уточняться нормативной документацией конкретного объекта.
VIBRATION_ZONES = [
    (0.0, 2.8, "A"),
    (2.8, 4.5, "B"),
    (4.5, 7.1, "C"),
    (7.1, 999.0, "D"),
]


def vibration_zone(v):
    for lo, hi, zone in VIBRATION_ZONES:
        if lo <= v < hi:
            return zone
    return "D"


def simulate_series(rng, degrade_risk, base_vibration=None, base_temp=None):
    base_vibration = base_vibration if base_vibration is not None else rng.uniform(1.2, 2.2)
    base_temp = base_temp if base_temp is not None else rng.uniform(48, 58)
    base_load = rng.uniform(70, 95)

    will_degrade = rng.random() < degrade_risk
    degrade_start = int(N_DAYS * rng.uniform(0.55, 0.85)) if will_degrade else None
    drift_rate = rng.uniform(0.02, 0.06) if will_degrade else 0.0  # мм/с прироста в сутки после старта
    temp_drift_rate = rng.uniform(0.05, 0.15) if will_degrade else 0.0

    vib, temp, load = [], [], []
    for day in range(N_DAYS):
        v_noise = rng.normal(0, 0.12)
        t_noise = rng.normal(0, 0.7)
        l_noise = rng.normal(0, 3)

        drift_v = 0.0
        drift_t = 0.0
        if will_degrade:
            # слабый предвестник с первых суток эксплуатации + основной дрейф после
            # видимого начала деградации подшипника
            precursor_v = 0.012 * drift_rate * day
            precursor_t = 0.02 * temp_drift_rate * day
            main_v = drift_rate * (day - degrade_start) ** 1.1 / 6 if day >= degrade_start else 0.0
            main_t = temp_drift_rate * (day - degrade_start) ** 1.1 / 5 if day >= degrade_start else 0.0
            drift_v = precursor_v + main_v
            drift_t = precursor_t + main_t

        vib.append(round(float(max(0.1, base_vibration + v_noise + drift_v)), 3))
        temp.append(round(float(base_temp + t_noise + drift_t), 2))
        load.append(round(float(np.clip(base_load + l_noise, 0, 100)), 1))

    return {
        "days": list(range(N_DAYS)),
        "vibration_mm_s": vib,
        "bearing_temp_c": temp,
        "load_pct": load,
        "will_degrade": bool(will_degrade),
        "degrade_start_day": degrade_start,
    }


if __name__ == "__main__":
    rng = np.random.default_rng(0)
    s = simulate_series(rng, degrade_risk=0.8)
    print("will_degrade:", s["will_degrade"], "start:", s["degrade_start_day"])
    print("vib[0:5]:", s["vibration_mm_s"][:5], "vib[-5:]:", s["vibration_mm_s"][-5:])
