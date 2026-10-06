"""Neutral view over a save file of any supported engine."""
from __future__ import annotations

from dataclasses import dataclass


class SaveError(Exception):
    pass


@dataclass
class ActorView:
    id: int
    name: str
    level: int | None
    exp: int | None
    hp: int | None
    mp: int | None
    class_id: int | None = None


INVENTORY_KINDS = ("items", "weapons", "armors")
MAX_GOLD = {"MV": 99999999, "MZ": 99999999, "ACE": 99999999, "VX": 9999999}


class SaveDoc:
    """Base class. Subclasses edit the native tree in place; nothing is rebuilt."""

    engine = ""
    path = None
    readonly: str | None = None       # reason, when the file cannot be written back safely
    dirty = False

    # switches / variables: 1-based ids like the editor
    def switch_count(self) -> int: raise NotImplementedError
    def get_switch(self, i: int) -> bool: raise NotImplementedError
    def set_switch(self, i: int, v: bool) -> None: raise NotImplementedError
    def variable_count(self) -> int: raise NotImplementedError
    def get_variable(self, i: int): raise NotImplementedError
    def set_variable(self, i: int, v) -> None: raise NotImplementedError
    # party
    def gold(self) -> int | None: raise NotImplementedError
    def set_gold(self, v: int) -> None: raise NotImplementedError
    def party_ids(self) -> list[int]: raise NotImplementedError
    def actor(self, actor_id: int) -> ActorView | None: raise NotImplementedError
    def set_actor(self, actor_id: int, **fields) -> None: raise NotImplementedError
    def inventory(self, kind: str) -> dict[int, int]: raise NotImplementedError
    def set_item(self, kind: str, item_id: int, count: int) -> None: raise NotImplementedError
    def position(self) -> tuple[int, int, int] | None: raise NotImplementedError
    def set_position(self, map_id: int | None = None, x: int | None = None, y: int | None = None) -> None: raise NotImplementedError
    def playtime(self) -> str | None: return None
    def save(self, backup: bool = True) -> None: raise NotImplementedError

    def _check_writable(self) -> None:
        if self.readonly:
            raise SaveError(f"read-only: {self.readonly}")

    def _touch(self) -> None:
        self._check_writable()
        self.dirty = True

    def clamp_gold(self, v: int) -> int:
        return max(0, min(int(v), MAX_GOLD.get(self.engine, 99999999)))
