"""Wire contract audited against AutoTrust's serve_decide.py (see docs/contract.md)."""

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class DecideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    kind: Literal["noul", "score", "choice"]
    state: str | dict[str, JsonValue] | list[JsonValue] = ""
    question: str
    options: list[str] | None = Field(
        default=None, description="choice: 2–256 options, in order"
    )
    strategy: Literal["auto", "single", "tournament", "permute"] = "auto"
    thinking: Literal["default", "off", "auto", "on"] = "default"
    threshold: float | None = None
    think_budget: int | None = None
    reasoning_effort: str | None = None
    chat_template_kwargs: dict[str, JsonValue] | None = None
    return_reasoning: bool = False
    debug: bool = False
    system2_only: bool = False

    @model_validator(mode="after")
    def validate_choice(self) -> "DecideRequest":
        if self.kind == "choice" and not 2 <= len(self.options or []) <= 256:
            raise ValueError("choice requires 2–256 string options")
        return self

    def wire_body(self) -> dict[str, Any]:
        # Do not pin backend defaults or turn omitted values into null.
        return self.model_dump(exclude_unset=True)


class ContractError(ValueError):
    pass


def validate_decision(data: dict[str, Any], request: DecideRequest) -> None:
    """Check alignment without rounding, sorting, recalibrating or replacing backend fields."""
    expected = (
        request.options
        if request.kind == "choice"
        else (
            ["false", "true"] if request.kind == "noul" else [str(i) for i in range(6)]
        )
    )
    if data.get("kind") != request.kind or data.get("options") != expected:
        raise ContractError("backend kind/options do not match the request")
    probabilities = data.get("probabilities")
    if not isinstance(probabilities, list) or len(probabilities) != len(expected or []):
        raise ContractError("backend probabilities/options lengths differ")
    if any(
        type(p) not in (float, int) or not math.isfinite(p) or not 0 <= p <= 1
        for p in probabilities
    ) or not math.isclose(sum(probabilities), 1.0, abs_tol=0.001):
        raise ContractError(
            "backend probabilities are not a finite probability distribution"
        )
    index = data.get("choice_index")
    if type(index) is not int or not 0 <= index < len(probabilities):
        raise ContractError("backend choice_index is out of range")
    if data.get("choice") != expected[index] or index != max(
        range(len(probabilities)), key=probabilities.__getitem__
    ):
        raise ContractError("backend choice does not match its distribution")
    if request.system2_only:
        if data.get("system") != 2:
            raise ContractError("system2_only response is missing system=2")
    elif data.get("protocol") != "jev27-bare-v1":
        raise ContractError("backend protocol is not jev27-bare-v1")
