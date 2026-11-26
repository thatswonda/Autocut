"""Shared building blocks for every AutoEdit schema."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Tolerance for float comparisons on timelines (half a frame at 60 fps).
TIME_EPSILON = 1.0 / 120.0

NonNegTime = Annotated[float, Field(ge=0.0, description="Seconds, >= 0")]
PosTime = Annotated[float, Field(gt=0.0, description="Seconds, > 0")]
Reason = Annotated[str, Field(max_length=500, description="Human-readable explanation shown in the UI")]
Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-:.]+$")]


class Aspect(StrEnum):
    VERTICAL = "9:16"
    SQUARE = "1:1"
    WIDE = "16:9"
    PORTRAIT = "4:5"


class StrictModel(BaseModel):
    """Base model: unknown keys are rejected so malformed LLM output fails loudly."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, str_strip_whitespace=True)


M = TypeVar("M", bound=BaseModel)


class SchemaValidationError(ValueError):
    """Raised by ``parse_model``; ``feedback`` is a compact message suitable for an LLM retry prompt."""

    def __init__(self, model_name: str, errors: list[dict[str, Any]]):
        self.model_name = model_name
        self.errors = errors
        super().__init__(self.feedback)

    @property
    def feedback(self) -> str:
        lines = [f"{self.model_name} failed validation with {len(self.errors)} error(s):"]
        for err in self.errors[:25]:
            loc = ".".join(str(p) for p in err.get("loc", ())) or "<root>"
            lines.append(f"- {loc}: {err.get('msg', 'invalid')}")
        if len(self.errors) > 25:
            lines.append(f"- ... and {len(self.errors) - 25} more")
        return "\n".join(lines)


def parse_model(model: type[M], data: str | bytes | dict[str, Any]) -> M:
    """Parse JSON text or a dict into ``model`` and raise a ``SchemaValidationError`` on failure.

    Used for every Claude response: validate, retry once with ``err.feedback``, then fail the stage.
    """
    try:
        if isinstance(data, str | bytes):
            return model.model_validate_json(data)
        return model.model_validate(data)
    except ValidationError as exc:
        errors = [
            {"loc": e["loc"], "msg": e["msg"], "type": e["type"]}
            for e in exc.errors(include_url=False, include_context=False)
        ]
        raise SchemaValidationError(model.__name__, errors) from exc
    except json.JSONDecodeError as exc:  # pragma: no cover - pydantic wraps this, kept for safety
        raise SchemaValidationError(model.__name__, [{"loc": (), "msg": str(exc)}]) from exc
