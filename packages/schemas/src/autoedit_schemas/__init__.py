"""AutoEdit data contracts. Every AI output and every pipeline artifact validates against these models."""

from .common import Aspect, SchemaValidationError, parse_model
from .composition import Composition
from .edl import EDL, LintIssue, lint_edl
from .media import ACCEPTED_CONTAINERS, MediaInfo
from .signals import Signals
from .transcript import Transcript

__all__ = [
    "ACCEPTED_CONTAINERS",
    "EDL",
    "Aspect",
    "Composition",
    "LintIssue",
    "MediaInfo",
    "SchemaValidationError",
    "Signals",
    "Transcript",
    "lint_edl",
    "parse_model",
]


def json_schemas() -> dict[str, dict[str, object]]:
    """JSON Schema for each contract, for the web client (Zod/TS codegen) and LLM tool definitions."""
    return {
        "edl": EDL.model_json_schema(),
        "composition": Composition.model_json_schema(by_alias=True),
        "signals": Signals.model_json_schema(),
        "transcript": Transcript.model_json_schema(),
        "media_info": MediaInfo.model_json_schema(),
    }
