from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

class ReceiveOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    grams: int
    scent: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    declaration: list | dict
    dryer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    due_date: str | int | None = None
    due: str | int | None = None
    due_on: str | int | None = None
    due_at: str | int | None = None
    deadline: str | int | None = None
    delivery_date: str | int | None = None
    required_date: str | int | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_due_alias(cls, value: Any) -> Any:
        if isinstance(value, dict):
            value = dict(value)
            if value.get("due_date") in (None, ""):
                for key in ("due", "due_on", "due_at", "deadline", "delivery_date", "required_date"):
                    if value.get(key) not in (None, ""):
                        value["due_date"] = value[key]
                        break
        return value


class ClearHold(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_ref: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
