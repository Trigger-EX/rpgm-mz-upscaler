"""Pure scaling math. One uniform world factor N (a multiple of 1/8) is used for everything."""
from __future__ import annotations

import math
from dataclasses import dataclass, field


def quantize(n: float) -> float:
    """Round down to a multiple of 1/8 (never below 1/8)."""
    return max(1, math.floor(n * 8 + 1e-9)) / 8


def parse_scale(value: str | float, orig: tuple[int, int], target: tuple[int, int]) -> float:
    if isinstance(value, str) and value.strip().lower() == "fit":
        return quantize(min(target[0] / orig[0], target[1] / orig[1]))
    n = float(value)
    if n <= 0:
        raise ValueError("scale must be positive")
    return quantize(n)


def scaled(size: int, n: float) -> int:
    return max(1, int(round(size * n)))


def cover_size(w: int, h: int, n: float, target: tuple[int, int]) -> tuple[int, int]:
    """Size for images that must cover the screen: aspect kept, never below the world scale."""
    f = max(n, target[0] / w, target[1] / h)
    return math.ceil(w * f - 1e-9), math.ceil(h * f - 1e-9)


def _even(v: float) -> int:
    return int(round(v / 2.0)) * 2


@dataclass
class ScalePlan:
    n: float
    orig: tuple[int, int]
    ui_orig: tuple[int, int]
    target: tuple[int, int]
    tile_orig: int = 48
    ui_fill: bool = False
    anchor: str = "center"
    warnings: list[str] = field(default_factory=list)

    @property
    def tile(self) -> int:
        return scaled(self.tile_orig, self.n)

    @property
    def ui_area(self) -> tuple[int, int]:
        if self.ui_fill:
            return self.target
        return (min(_even(self.ui_orig[0] * self.n), self.target[0]),
                min(_even(self.ui_orig[1] * self.n), self.target[1]))

    @property
    def offset(self) -> tuple[int, int]:
        if self.anchor == "topleft":
            return 0, 0
        return (int(round((self.target[0] - self.orig[0] * self.n) / 2)),
                int(round((self.target[1] - self.orig[1] * self.n) / 2)))

    def check(self) -> list[str]:
        w: list[str] = []
        if self.orig[0] * self.n > self.target[0] + 1 or self.orig[1] * self.n > self.target[1] + 1:
            w.append(f"Original screen {self.orig[0]}x{self.orig[1]} at x{self.n:g} exceeds the target "
                     f"{self.target[0]}x{self.target[1]}; fewer UI lines will be visible.")
        if (self.tile_orig * self.n) % 2:
            w.append("Scaled tile size is odd; autotile half-tiles may misalign.")
        return w
