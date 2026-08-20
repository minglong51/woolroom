"""Channel protocol. A channel is how the pet's surface reaches a human.

v1 only ships WebApp. An iOS app can come later as a separate channel
implementation over the same shape.
"""

from __future__ import annotations

from typing import Any, Protocol


class Channel(Protocol):
    name: str

    async def broadcast(self, pet_id: str, event: dict[str, Any]) -> None:
        """Push an event to every surface currently observing this pet."""
        ...
