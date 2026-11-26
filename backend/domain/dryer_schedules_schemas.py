from typing import Any

from pydantic import BaseModel, ConfigDict


class _Flexible(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)


class DraftSchedule(_Flexible):
    code: str | None = None
    schedule_code: str | None = None
    capacity_a_g: int | float | None = None
    capacity_b_g: int | float | None = None
    capacities: dict[str, Any] | list[Any] | None = None
    orders: list[Any] | None = None
    order_refs: list[str] | None = None
    blocked_racks: list[str] | None = None
    stopped_racks: list[str] | None = None
    primary_lot: str | None = None


class PlanSchedule(DraftSchedule):
    pass


class Reschedule(DraftSchedule):
    pass


class FinalizeDraft(_Flexible):
    code: str | None = None
    schedule_code: str | None = None
    draft_code: str | None = None
    token: str | None = None
    draft_token: str | None = None
    version: int | None = None


class StartOrder(_Flexible):
    code: str | None = None
    order_ref: str | None = None
    schedule_code: str | None = None
    draft_code: str | None = None
    token: str | None = None
    dryer: str | None = None
    rack: str | None = None
    wet_grams: int | float | None = None
    grams: int | float | None = None


class StopDryer(_Flexible):
    dryer: str | None = None
    rack: str | None = None
    code: str | None = None
    active: bool | None = False
    capacity_g: int | float | None = None


class RackStatus(StopDryer):
    pass
