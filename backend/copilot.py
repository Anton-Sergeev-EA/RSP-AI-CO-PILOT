"""
RSP COPILOT — чат-ассистент дежурного диспетчера (RAG, встроенный в SCADA/HMI).

Честная реализация RAG без вызова внешней LLM (доступа к внешним LLM API из
песочницы нет): (1) извлечение из свободного текста вопроса упоминания
агрегата/станции — настоящий, работающий разбор текста; (2) retrieval —
настоящий поиск релевантных записей локальной базы эксплуатационных знаний
(knowledge_base/kb.py); (3) генерация ответа — шаблонная сборка найденных
фрагментов с подстановкой live-данных конкретного агрегата из fleet.json.
В продакшене шаг (3) заменяется одним вызовом LLM поверх того же retrieval —
контекст уже готовится в нужном виде (см. build_context ниже).
"""
import os
import re
import json

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "knowledge_base"))
from kb import retrieve, retrieve_by_ids  # noqa: E402

HERE = os.path.dirname(__file__)
FLEET_PATH = os.path.join(HERE, "data", "fleet", "fleet.json")

PLANT_ALIASES = {
    "Богучанская ГЭС": ["богучанск"],
    "Саяно-Шушенская ГЭС": ["саяно-шушенск", "саянск", "сшгэс"],
    "Балаковская АЭС": ["балаковск"],
    "Няганьская ГРЭС": ["няганьск", "нягань"],
    "Загорская ГАЭС": ["загорск"],
    "Волжская ГЭС": ["волжск"],
}

UNIT_ID_RE = re.compile(r"\b(ГА|ТГ)\s*-?\s*(\d{1,2})\b", re.IGNORECASE)
ZONE_RE = re.compile(r"зон[а-яё]*\s*([abcdABCD])\b")

ZONE_KB_ID = {"A": "kb_zone_a", "B": "kb_zone_b", "C": "kb_zone_c", "D": "kb_zone_d"}

STATUS_RU = {"OK": "в норме", "WARNING": "предупреждение", "CRITICAL": "критическое"}


def _load_fleet():
    with open(FLEET_PATH, encoding="utf-8") as f:
        return json.load(f)


def extract_unit_id(text):
    m = UNIT_ID_RE.search(text)
    if not m:
        return None
    return f"{m.group(1).upper()}-{m.group(2)}"


def extract_plant(text):
    low = text.lower()
    for plant, aliases in PLANT_ALIASES.items():
        if any(a in low for a in aliases):
            return plant
    return None


def _wants_critical_list(text):
    low = text.lower()
    keywords = ["критическ", "авари", "какие агрегат", "проблемн", "список"]
    return any(k in low for k in keywords)


def _find_unit(fleet, unit_id, plant_hint=None):
    candidates = [u for u in fleet if u["unit_id"] == unit_id]
    if plant_hint:
        exact = [u for u in candidates if u["plant"] == plant_hint]
        if exact:
            return exact[0]
    return candidates[0] if candidates else None


def _explain_unit(unit, question=""):
    cpp = unit.get("cpp_realtime_analysis", {})
    ai = unit.get("ai_early_warning", {})
    status = unit["health_status"]
    trigger = cpp.get("trigger", "none")

    parts = [
        f"{unit['unit_id']} ({unit['plant']}, {unit['plant_type']}, "
        f"{unit['rated_capacity_mw']:.0f} МВт): статус — {STATUS_RU.get(status, status)}. "
        f"Текущая зона виброскорости: {unit['current_vibration_zone']}."
    ]

    tag_hints = []
    if status == "CRITICAL":
        tag_hints.append("kb_action_critical")
        if trigger == "absolute_threshold":
            day = cpp.get("absolute_threshold_day", -1)
            parts.append(
                f"Сработал абсолютный порог (зона C) на {day}-е сутки эксплуатации — "
                f"классический детектор защиты."
            )
            tag_hints.append("kb_absolute_trigger")
        elif trigger == "trend":
            day = cpp.get("trend_anomaly_day", -1)
            parts.append(
                f"Статистический тренд-детектор зафиксировал устойчивое отклонение от "
                f"пуско-наладочной базовой линии начиная примерно с {day}-х суток."
            )
            tag_hints.append("kb_trend_trigger")
    elif status == "WARNING":
        tag_hints.append("kb_action_warning")
        parts.append(
            "Абсолютный и тренд-детекторы пока не сработали, но есть ранние признаки "
            "отклонения — см. оценку AI-модели ниже."
        )
    else:
        parts.append("Классические детекторы отклонений не фиксируют.")

    prob = ai.get("probability")
    if prob is not None:
        lead = unit.get("lead_time_days_vs_classic")
        lead_txt = ""
        if lead:
            lead_txt = f" — это на {lead} суток раньше, чем сработал бы классический детектор"
        if prob >= 0.5:
            parts.append(
                f"AI-модель раннего предупреждения оценивает вероятность развивающейся "
                f"деградации в {prob*100:.0f}% уже по первым 90 суткам телеметрии{lead_txt}."
            )
            tag_hints.append("kb_ai_early_warning")
        else:
            parts.append(f"AI-модель раннего предупреждения: вероятность деградации низкая ({prob*100:.0f}%).")

    rul = cpp.get("rul_days_estimate", -1)
    if rul and rul > 0 and status != "OK":
        parts.append(f"Грубая оценка остаточного ресурса до границы зоны C (RUL): ~{rul:.0f} суток.")
        tag_hints.append("kb_rul")

    kb_docs = retrieve_by_ids(tag_hints)
    if not kb_docs and status == "OK":
        kb_docs = []
    elif not kb_docs:
        kb_docs = retrieve(question or status, top_k=2)

    for d in kb_docs[:3]:
        parts.append(d["text"])

    return {
        "answer": " ".join(parts),
        "sources": [d["id"] for d in kb_docs[:3]],
        "unit_id": unit["unit_id"],
    }


def _plant_summary(fleet, plant, question=""):
    units = [u for u in fleet if u["plant"] == plant]
    n_crit = [u["unit_id"] for u in units if u["health_status"] == "CRITICAL"]
    n_warn = [u["unit_id"] for u in units if u["health_status"] == "WARNING"]
    parts = [f"{plant}: всего агрегатов под наблюдением — {len(units)}."]
    if n_crit:
        parts.append(f"В критическом состоянии: {', '.join(n_crit)}.")
    if n_warn:
        parts.append(f"С предупреждением: {', '.join(n_warn)}.")
    if not n_crit and not n_warn:
        parts.append("Все агрегаты станции в норме.")
    docs = retrieve(question or "статус станции агрегаты", top_k=2)
    for d in docs:
        parts.append(d["text"])
    return {"answer": " ".join(parts), "sources": [d["id"] for d in docs], "plant": plant}


def _critical_list(fleet):
    crit = [u for u in fleet if u["health_status"] == "CRITICAL"]
    warn = [u for u in fleet if u["health_status"] == "WARNING"]
    parts = []
    if crit:
        items = ", ".join(f"{u['unit_id']} ({u['plant']})" for u in crit)
        parts.append(f"В критическом состоянии сейчас {len(crit)} агрегат(ов): {items}.")
    else:
        parts.append("Агрегатов в критическом состоянии сейчас нет.")
    if warn:
        items = ", ".join(f"{u['unit_id']} ({u['plant']})" for u in warn)
        parts.append(f"С предупреждением: {len(warn)} — {items}.")
    docs = retrieve_by_ids(["kb_action_critical", "kb_action_warning"])
    for d in docs:
        parts.append(d["text"])
    return {"answer": " ".join(parts), "sources": [d["id"] for d in docs]}


def _general_answer(question):
    m = ZONE_RE.search(question)
    if m:
        kb_id = ZONE_KB_ID[m.group(1).upper()]
        docs = retrieve_by_ids([kb_id])
        return {"answer": " ".join(d["text"] for d in docs), "sources": [d["id"] for d in docs]}

    docs = retrieve(question, top_k=3)
    if not docs:
        docs = retrieve_by_ids(["kb_what_is_rsp_copilot"])
    parts = [d["text"] for d in docs]
    if not parts:
        parts = [
            "Не нашёл в базе знаний точного ответа на этот вопрос. Уточните, пожалуйста, "
            "агрегат (например, «ГА-3») или станцию, о которой идёт речь."
        ]
    return {"answer": " ".join(parts), "sources": [d["id"] for d in docs]}


def ask(question: str) -> dict:
    fleet = _load_fleet()
    unit_id = extract_unit_id(question)
    plant = extract_plant(question)

    if unit_id:
        unit = _find_unit(fleet, unit_id, plant_hint=plant)
        if unit:
            return {**_explain_unit(unit, question), "intent": "unit_status"}
        return {
            "answer": f"Агрегат {unit_id} не найден в текущем парке под наблюдением.",
            "sources": [],
            "intent": "unit_not_found",
        }

    if _wants_critical_list(question):
        return {**_critical_list(fleet), "intent": "fleet_critical_list"}

    if plant:
        return {**_plant_summary(fleet, plant, question), "intent": "plant_status"}

    return {**_general_answer(question), "intent": "general"}


if __name__ == "__main__":
    for q in [
        "Что с ГА-3 на Волжской ГЭС?",
        "какие агрегаты сейчас в критическом состоянии?",
        "что происходит на Саяно-Шушенской ГЭС?",
        "как работает модель раннего предупреждения?",
        "что такое зона C?",
    ]:
        print("Q:", q)
        print(json.dumps(ask(q), ensure_ascii=False, indent=2))
        print("---")
