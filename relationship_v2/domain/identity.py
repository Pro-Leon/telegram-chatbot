"""Fan identity scope. Fail-closed on unknown scope."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FanScope(BaseModel):
    """Mandatory (creator_id, user_id) scope for every V2 read/mutation."""

    creator_id: int = Field(gt=0)
    user_id: int = Field(gt=0)

    model_config = {"frozen": True}

    def as_tuple(self) -> tuple[int, int]:
        return (self.creator_id, self.user_id)
