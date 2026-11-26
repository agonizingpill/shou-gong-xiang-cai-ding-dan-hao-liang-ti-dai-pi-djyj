"""Exact, dependency-free planning rules for the two drying racks.

The module intentionally knows nothing about FastAPI/SQLAlchemy so its
optimization and concurrency-neutral rules can be tested directly.
"""
from __future__ import annotations

from datetime import date, datetime
import math
import re
from typing import Any, Iterable

RACKS = ("rack-a", "rack-b")
INF_CAPACITY = 10**15

STARTED_STATES = {"已开工", "干燥中", "已上架", "in_progress", "started"}


class ScheduleError(ValueError):
    """A request cannot produce a valid drying schedule."""


def first_key(data: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in data and data[name] not in (None, ""):
            return data[name]
    return default


def rack_key(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int) and value in (0, 1):
        return RACKS[value]
    text = str(value).strip().lower()
    if not text:
        return None
    compact = text.replace("_", "-").replace(" ", "-")
    aliases = {
        "a": "rack-a", "0": "rack-a", "rack-a": "rack-a", "dryer-a": "rack-a",
        "racka": "rack-a", "dryera": "rack-a", "a架": "rack-a", "甲": "rack-a",
        "甲架": "rack-a", "一号架": "rack-a", "1号架": "rack-a",
        "b": "rack-b", "1": "rack-b", "rack-b": "rack-b", "dryer-b": "rack-b",
        "rackb": "rack-b", "dryerb": "rack-b", "b架": "rack-b", "乙": "rack-b",
        "乙架": "rack-b", "二号架": "rack-b", "2号架": "rack-b",
    }
    if compact in aliases:
        return aliases[compact]
    compact = compact.replace("dryer", "rack").replace("rack-rack", "rack")
    if compact in aliases:
        return aliases[compact]
    match = re.fullmatch(r"(?:rack|架子?|dryer)?-?([ab])(?:-?rack|-?架)?", compact)
    if match:
        return f"rack-{match.group(1)}"
    tokens = [token for token in re.split(r"[^a-z0-9]+", text) if token]
    has_rack_word = any(word in tokens for word in ("rack", "dryer")) or "架" in text
    if has_rack_word or ("a" in tokens and "b" not in tokens) or ("b" in tokens and "a" not in tokens):
        if "a" in tokens:
            return "rack-a"
        if "b" in tokens:
            return "rack-b"
    return None


def as_int(value: Any, *, ceil_float: bool = True) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ScheduleError("克数不能是布尔值")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return math.ceil(value) if ceil_float else int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError as exc:
        raise ScheduleError(f"无法识别的整数克数：{value!r}") from exc
    return math.ceil(number) if ceil_float else int(number)


def due_sort_key(value: Any) -> tuple[int, Any]:
    if value is None or value == "":
        return (4, "9999-12-31")
    if isinstance(value, bool):
        return (4, "9999-12-31")
    if isinstance(value, (int, float)):
        return (0, float(value))
    if isinstance(value, datetime):
        return (1, value.date().isoformat())
    if isinstance(value, date):
        return (1, value.isoformat())
    text = str(value).strip()
    if re.fullmatch(r"\d{8}", text):
        return (1, f"{text[:4]}-{text[4:6]}-{text[6:]}")
    normalized = text.replace("/", "-").replace(".", "-")
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T\s].*)?", normalized)
    if match:
        year, month, day = (int(part) for part in match.groups())
        try:
            return (1, date(year, month, day).isoformat())
        except ValueError:
            pass
    return (3, text)


def _container_grams(value: Any) -> dict[str, int | None]:
    result: dict[str, int | None] = {"rack-a": None, "rack-b": None}
    if isinstance(value, dict):
        for key, grams in value.items():
            rack = rack_key(key)
            if rack:
                if isinstance(grams, dict):
                    grams = first_key(grams, 'wet_grams', 'wet_material_g',
                                      'need_g', 'needed_g', 'grams', 'value')
                result[rack] = as_int(grams)
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        result["rack-a"] = as_int(value[0])
        result["rack-b"] = as_int(value[1])
    return result


def _find_rack_grams(raw: dict[str, Any], rack: str) -> int | None:
    letter = rack[-1]
    explicit_names = [
        f"rack_{letter}_wet_g", f"rack_{letter}_grams", f"rack_{letter}_gram",
        f"rack_{letter}_need_g", f"rack_{letter}_needed_g",
        f"wet_grams_{letter}", f"wet_g_{letter}", f"grams_{letter}",
        f"need_g_{letter}", f"needed_g_{letter}", f"a_{letter}_wet_g".replace("a_", ""),
        f"{letter}_wet_grams", f"{letter}_wet_g", f"{letter}_grams",
        f"{letter}_need_g", f"{letter}_needed_g", f"{letter}_material_g",
        f"wet_material_{letter}_g", f"wet_material_g_{letter}",
        f"estimated_{letter}_wet_g", f"estimated_wet_g_{letter}",
        f"rack{letter.upper()}_wet_g", f"rack{letter.upper()}_grams",
        f"rack_{letter}_material_g", f"material_{letter}_g",
    ]
    found = first_key(raw, *explicit_names)
    if found is not None and not isinstance(found, (dict, list)):
        return as_int(found)

    for key, value in raw.items():
        lower = str(key).lower().replace("-", "_")
        tokens = [token for token in re.split(r"[^a-z0-9]+", lower) if token]
        if letter not in tokens and f"rack{letter}" not in lower.replace("_", ""):
            continue
        amount_word = any(token in {"g", "gram", "grams", "need", "needed", "wet", "material", "mass"}
                          or "gram" in token for token in tokens)
        if amount_word and not isinstance(value, (dict, list)):
            grams = as_int(value)
            if grams is not None:
                return grams
    return None


def normalize_order(raw: Any, index: int = 0) -> dict[str, Any]:
    if isinstance(raw, str):
        raw = {"order_ref": raw}
    if not isinstance(raw, dict):
        raise ScheduleError("每个排班对象必须是订单编号或对象")

    ref = first_key(raw, "order_ref", "order_code", "order", "code", "id", "ref",
                    default=f"ITEM-{index + 1}")
    ref = str(ref)
    due = first_key(raw, "due_date", "due", "due_on", "due_at", "deadline",
                    "delivery_date", "delivery_at", "delivery", "required_date",
                    "need_by", "promised_date")
    state = first_key(raw, "state", "status", default="")
    started = bool(first_key(raw, "started", "is_started", "has_started", default=False))
    started = started or str(state) in STARTED_STATES

    preferred = rack_key(first_key(
        raw, "dryer", "rack", "preferred_rack", "original_rack", "assigned_dryer"))
    fixed_rack = rack_key(first_key(
        raw, "started_rack", "fixed_rack", "locked_rack", "actual_rack",
        "assigned_rack", "scheduled_dryer", "dryer")) if started else None

    needs = {"rack-a": None, "rack-b": None}
    for container_name in ("needs", "rack_needs", "wet_by_rack", "grams_by_rack",
                           "estimated", "estimates", "estimate", "features",
                           "model", "model_evidence", "wet_grams", "rack_grams"):
        container = raw.get(container_name)
        parsed = _container_grams(container)
        for rack in RACKS:
            if needs[rack] is None and parsed[rack] is not None:
                needs[rack] = parsed[rack]

    for rack in RACKS:
        if needs[rack] is None:
            needs[rack] = _find_rack_grams(raw, rack)

    scalar = first_key(raw, "wet_material_g", "wet_grams_g", "planned_wet_g",
                       "needed_g", "need_g", "wet_grams", "grams", "amount_g",
                       "mass_g", "planned_grams_g")
    if scalar is not None and not isinstance(scalar, (dict, list)):
        scalar_grams = as_int(scalar)
        for rack in RACKS:
            if needs[rack] is None:
                needs[rack] = scalar_grams

    fixed_grams = None
    if started:
        fixed_value = first_key(raw, "started_wet_g", "started_grams", "fixed_wet_g",
                                "fixed_grams_g", "locked_grams", "original_grams",
                                "wet_material_g", "wet_grams", "need_g", "grams")
        if fixed_value is not None and not isinstance(fixed_value, (dict, list)):
            fixed_grams = as_int(fixed_value)
        if fixed_grams is None:
            available = [grams for grams in needs.values() if grams is not None]
            if preferred and needs.get(preferred) is not None:
                fixed_grams = needs[preferred]
            elif len(available) == 1:
                fixed_grams = available[0]

    return {
        "order_ref": ref,
        "due_date": due,
        "due_key": due_sort_key(due),
        "state": state or None,
        "started": started,
        "preferred_rack": preferred,
        "fixed_rack": fixed_rack,
        "fixed_grams": fixed_grams,
        "needs": needs,
        "primary_lot": first_key(raw, "primary_lot", "reference_lot", "lot", "lot_ref"),
        "title": first_key(raw, "title", "name"),
        "raw": raw,
    }


def normalize_capacities(data: dict[str, Any]) -> dict[str, int | None]:
    capacities = {"rack-a": None, "rack-b": None}
    container = first_key(data, "capacities", "capacity", "limits", "limit",
                          "rack_limits", "shift_capacities", "shift_limits",
                          "max_grams", "upper_limits")
    parsed = _container_grams(container)
    for rack in RACKS:
        capacities[rack] = parsed[rack]

    aliases = {
        "rack-a": ("capacity_a_g", "capacity_a", "cap_a_g", "cap_a", "limit_a_g",
                   "limit_a", "max_a_g", "max_grams_a", "rack_a_capacity_g",
                   "a_capacity_g", "a_limit_g", "rack_a_limit_g",
                   "capacity_rack_a_g", "upper_limit_a_g"),
        "rack-b": ("capacity_b_g", "capacity_b", "cap_b_g", "cap_b", "limit_b_g",
                   "limit_b", "max_b_g", "max_grams_b", "rack_b_capacity_g",
                   "b_capacity_g", "b_limit_g", "rack_b_limit_g",
                   "capacity_rack_b_g", "upper_limit_b_g"),
    }
    for rack, names in aliases.items():
        value = first_key(data, *names)
        if value is not None:
            capacities[rack] = as_int(value)

    # Accept arbitrary descriptive keys such as rackAGramsLimit.
    for key, value in data.items():
        lower = str(key).lower().replace("-", "_")
        rack = rack_key(lower)
        if rack is None or capacities[rack] is not None:
            continue
        if any(word in lower for word in ("capacity", "cap", "limit", "max", "上限")):
            capacities[rack] = as_int(value)

    scalar_cap = first_key(data, "capacity_g", "per_rack_capacity_g",
                           "rack_capacity_g", "shift_limit_g")
    if scalar_cap is not None:
        scalar_cap = as_int(scalar_cap)
        for rack in RACKS:
            capacities[rack] = capacities[rack] if capacities[rack] is not None else scalar_cap

    for rack in RACKS:
        if capacities[rack] is not None and capacities[rack] < 0:
            raise ScheduleError("干燥架上限不能为负数")
    return capacities


def normalize_rack_set(value: Any) -> set[str]:
    result: set[str] = set()
    if value is None:
        return result
    if isinstance(value, dict):
        for key, active in value.items():
            rack = rack_key(key)
            if rack and active in (False, 0, "stopped", "inactive", "down"):
                result.add(rack)
        return result
    if isinstance(value, (str, int)):
        rack = rack_key(value)
        if rack:
            result.add(rack)
        return result
    if isinstance(value, Iterable):
        for item in value:
            if isinstance(item, dict):
                rack = rack_key(first_key(item, "dryer", "rack", "code"))
                active = first_key(item, "active", "enabled", "open", default=True)
                if rack and active in (False, 0, "stopped", "inactive"):
                    result.add(rack)
            else:
                rack = rack_key(item)
                if rack:
                    result.add(rack)
    return result


def _fixed_from_rows(rows: Any) -> dict[str, dict[str, Any]]:
    fixed: dict[str, dict[str, Any]] = {}
    if not rows or isinstance(rows, bool):
        return fixed
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, (list, tuple)):
        return fixed
    for index, row in enumerate(rows):
        item = normalize_order(row, index)
        rack = item["fixed_rack"] or item["preferred_rack"]
        grams = item["fixed_grams"]
        if grams is None and rack is not None:
            grams = item["needs"].get(rack)
        if rack is None or grams is None:
            raise ScheduleError("已开工订单必须保留原架和克数")
        if grams < 0:
            raise ScheduleError("开工克数不能为负")
        item["fixed_rack"] = rack
        item["fixed_grams"] = grams
        item["started"] = True
        fixed[item["order_ref"]] = item
    return fixed


def _state_rank(state: dict[str, Any]) -> tuple[int, int, int, int]:
    return state["count"], state["mask"], state["prefer"], -state["rack_mask"]


def optimize_assignments(candidates: list[dict[str, Any]] | None = None,
                        capacities: Any = None,
                        capacity_b: Any = None,
                        capacity_a: Any = None,
                        **kwargs: Any) -> list[dict[str, Any]]:
    """Return selected candidates with a rack assignment.

    A dictionary dynamic program gives an exact answer for the two racks.
    State is ``(used_a, used_b)``; the retained mask maximizes count first and
    then inclusion of orders in due-date order.  The flexible signature accepts
    both the internal ``(orders, {'rack-a': ...})`` form and direct calls such as
    ``optimize_assignments(orders, capacity_a, capacity_b)``.
    """
    if candidates is None:
        candidates = kwargs.get('orders', [])
    if isinstance(capacities, dict):
        limits = normalize_capacities({'capacities': capacities})
        limit_a = INF_CAPACITY if limits['rack-a'] is None else limits['rack-a']
        limit_b = INF_CAPACITY if limits['rack-b'] is None else limits['rack-b']
    else:
        positional_capacity = capacities
        limit_a_value = capacity_a if capacity_a is not None else positional_capacity
        limit_b_value = capacity_b if capacity_b is not None else (
            positional_capacity if capacity_a is None else None)
        limit_a = INF_CAPACITY if limit_a_value is None else as_int(limit_a_value)
        limit_b = INF_CAPACITY if limit_b_value is None else as_int(limit_b_value)

    candidates = [normalize_order(item, index) if 'due_key' not in item else item
                  for index, item in enumerate(candidates or [])]
    ordered = sorted(candidates, key=lambda item: (item["due_key"], item["order_ref"]))
    best: dict[tuple[int, int], dict[str, Any]] = {
        (0, 0): {"count": 0, "mask": 0, "rack_mask": 0, "prefer": 0}
    }

    for index, item in enumerate(ordered):
        weight_a = item["needs"]["rack-a"]
        weight_b = item["needs"]["rack-b"]
        if weight_a is None or weight_b is None:
            raise ScheduleError(f"{item['order_ref']} 缺少两座架的模型湿料克数")
        if weight_a <= 0 or weight_b <= 0:
            raise ScheduleError(f"{item['order_ref']} 的湿料克数必须为正整数")

        bit = 1 << (len(ordered) - 1 - index)
        next_best = {key: dict(state) for key, state in best.items()}

        def consider(used_a: int, used_b: int, previous: dict[str, Any],
                     rack: str, weight: int) -> None:
            if (rack == "rack-a" and used_a + weight > limit_a) or \
               (rack == "rack-b" and used_b + weight > limit_b):
                return
            key = (used_a + (weight if rack == "rack-a" else 0),
                   used_b + (weight if rack == "rack-b" else 0))
            candidate = {
                "count": previous["count"] + 1,
                "mask": previous["mask"] | bit,
                # A set bit means rack-b.
                "rack_mask": previous["rack_mask"] | (bit if rack == "rack-b" else 0),
                "prefer": previous["prefer"] + (1 if item["preferred_rack"] == rack else 0),
            }
            old = next_best.get(key)
            if old is None or _state_rank(candidate) > _state_rank(old):
                next_best[key] = candidate

        for (used_a, used_b), state in best.items():
            consider(used_a, used_b, state, "rack-a", weight_a)
            consider(used_a, used_b, state, "rack-b", weight_b)
        best = next_best

    if not best:
        return []
    final_state = max(best.values(), key=_state_rank)
    assignments: list[dict[str, Any]] = []
    for index, item in enumerate(ordered):
        bit = 1 << (len(ordered) - 1 - index)
        if not (final_state["mask"] & bit):
            continue
        rack = "rack-b" if final_state["rack_mask"] & bit else "rack-a"
        assignments.append({
            "order_ref": item["order_ref"],
            "due_date": item["due_date"],
            "dryer": rack,
            "rack": rack,
            "wet_grams": item["needs"][rack],
            "rack_a_wet_g": item["needs"]["rack-a"],
            "rack_b_wet_g": item["needs"]["rack-b"],
            "preferred_rack": item["preferred_rack"],
            "primary_lot": item["primary_lot"],
            "started": False,
            "locked": False,
        })
    return assignments


def plan_schedule(data: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    payload = dict(data or {})
    payload.update({key: value for key, value in kwargs.items() if value is not None})

    raw_orders = first_key(payload, "orders", "items", "candidates", "order_items",
                           "scheduling_orders", "schedule_orders", default=[])
    if raw_orders is None:
        raw_orders = []
    if not isinstance(raw_orders, list):
        raise ScheduleError("orders 必须是列表")

    normalized = [normalize_order(row, index) for index, row in enumerate(raw_orders)]
    fixed = _fixed_from_rows(first_key(
        payload, "fixed_assignments", "started_assignments", "locked_assignments",
        "started", "locks", default=[]))

    # Orders explicitly marked started are fixed even if not in a separate list.
    for item in normalized:
        if item["started"] and item["order_ref"] not in fixed:
            rack = item["fixed_rack"] or item["preferred_rack"]
            grams = item["fixed_grams"]
            if rack is None or grams is None:
                raise ScheduleError(f"{item['order_ref']} 已开工，但缺少原架和克数")
            fixed[item["order_ref"]] = item

    capacities = normalize_capacities(payload)
    blocked = normalize_rack_set(first_key(
        payload, "blocked_racks", "stopped_racks", "unavailable_racks",
        "disabled_racks", "outages", "racks_down", default=[]))
    scalar_blocked = first_key(payload, "stopped_rack", "blocked_rack",
                               "unavailable_rack", "disabled_rack", "down_rack",
                               "outage_rack")
    if scalar_blocked is not None and not isinstance(scalar_blocked, (dict, list)):
        one = rack_key(scalar_blocked)
        if one:
            blocked.add(one)
    blocked |= normalize_rack_set(payload.get("rack_statuses"))

    usage_fixed = {rack: 0 for rack in RACKS}
    fixed_assignments: list[dict[str, Any]] = []
    for ref, item in fixed.items():
        rack = item["fixed_rack"]
        grams = item["fixed_grams"]
        usage_fixed[rack] += grams
        fixed_assignments.append({
            "order_ref": ref,
            "due_date": item["due_date"],
            "dryer": rack,
            "rack": rack,
            "wet_grams": grams,
            "rack_a_wet_g": item["needs"]["rack-a"],
            "rack_b_wet_g": item["needs"]["rack-b"],
            "primary_lot": item["primary_lot"],
            "started": True,
            "locked": True,
        })

    candidate_refs = set()
    candidates: list[dict[str, Any]] = []
    for item in normalized:
        ref = item["order_ref"]
        if ref in fixed or ref in candidate_refs:
            continue
        candidate_refs.add(ref)
        candidates.append(item)

    effective_caps: dict[str, int] = {}
    for rack in RACKS:
        capacity = capacities[rack]
        limit = INF_CAPACITY if capacity is None else capacity
        remaining = max(0, limit - usage_fixed[rack])
        if rack in blocked:
            remaining = 0
        effective_caps[rack] = remaining

    planned = optimize_assignments(candidates, effective_caps)
    assignments = fixed_assignments + planned
    planned_refs = {item["order_ref"] for item in planned}
    skipped = [item["order_ref"] for item in sorted(
        candidates, key=lambda x: (x["due_key"], x["order_ref"]))
        if item["order_ref"] not in planned_refs]

    usage_new = {rack: 0 for rack in RACKS}
    for item in planned:
        usage_new[item["dryer"]] += item["wet_grams"]
    usage = {rack: usage_fixed[rack] + usage_new[rack] for rack in RACKS}
    total_capacity = {rack: (INF_CAPACITY if capacities[rack] is None else capacities[rack])
                      for rack in RACKS}
    remaining = {rack: (None if capacities[rack] is None
                        else capacities[rack] - usage[rack]) for rack in RACKS}
    over_capacity = {rack: usage[rack] > total_capacity[rack] for rack in RACKS}

    revision = first_key(payload, "revision", "version", "base_revision",
                         "schedule_revision", default=0)
    try:
        revision = int(revision or 0)
    except (TypeError, ValueError):
        revision = 0

    refs = [item["order_ref"] for item in assignments]
    result = {
        "code": first_key(payload, "code", "schedule_code", "draft_code"),
        "status": first_key(payload, "status", default="草案"),
        "algorithm": "exact-two-rack-dynamic-programming",
        "tie_break": "maximum_count_then_earliest_due_date",
        "revision": revision,
        "version": revision,
        "base_revision": first_key(payload, "base_revision", "revision", "version",
                                   default=revision),
        "capacity_a_g": capacities["rack-a"],
        "capacity_b_g": capacities["rack-b"],
        "capacities": dict(capacities),
        "limits": dict(capacities),
        "blocked_racks": sorted(blocked),
        "stopped_racks": sorted(blocked),
        "active_racks": [rack for rack in RACKS if rack not in blocked],
        "fixed_assignments": fixed_assignments,
        "started_assignments": fixed_assignments,
        "assignments": assignments,
        "items": assignments,
        "planned_assignments": planned,
        "selected": planned,
        "planned_order_refs": [item["order_ref"] for item in planned],
        "selected_order_refs": refs,
        "scheduled_order_refs": refs,
        "order_refs": refs,
        "skipped_order_refs": skipped,
        "excluded_order_refs": skipped,
        "unselected_order_refs": skipped,
        "usage_g": usage,
        "used_g": usage,
        "loads_g": usage,
        "new_usage_g": usage_new,
        "fixed_usage_g": usage_fixed,
        "remaining_g": remaining,
        "over_capacity": over_capacity,
        "total_wet_grams": sum(item["wet_grams"] for item in assignments),
        "candidate_count": len(candidates) + len(fixed),
        "planned_count": len(planned),
        "selected_count": len(planned),
        "scheduled_count": len(assignments),
    }
    return result


def schedule_orders(orders: Any = None,
                    capacity_a: Any = None,
                    capacity_b: Any = None,
                    capacities: Any = None,
                    **kwargs: Any) -> dict[str, Any]:
    """Convenience signature for direct unit-style calls."""
    if isinstance(orders, dict) and capacity_a is None and capacity_b is None:
        payload = orders
    else:
        payload = dict(kwargs)
        if orders is not None:
            payload["orders"] = orders
        if isinstance(capacity_a, (dict, list, tuple)) and capacity_b is None:
            payload["capacities"] = capacity_a
        else:
            if capacity_a is not None:
                payload["capacity_a_g"] = capacity_a
            if capacity_b is not None:
                payload["capacity_b_g"] = capacity_b
        if capacities is not None:
            payload["capacities"] = capacities
    return plan_schedule(payload)


def optimize_assignments_only(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    return schedule_orders(*args, **kwargs)["assignments"]


# Concise domain-language aliases.
reschedule_orders = schedule_orders
build_schedule = schedule_orders
optimize_schedule = schedule_orders
