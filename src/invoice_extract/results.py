"""Result types: one Row per invoice, one Cell per column."""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

LEVELS = ("ok", "warning", "error")


@dataclass
class Box:
    """Where a value sits. `page` is 1-based; coordinates are 0..1 from the top-left."""

    page: int
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Check:
    name: str
    level: str
    message: str


@dataclass
class Cell:
    value: Any = None
    raw: str = ""
    page: Optional[int] = None
    box: Optional[Box] = None
    source: str = ""
    edited: bool = False
    checks: List[Check] = field(default_factory=list)


@dataclass
class Row:
    path: str
    kind: str = ""
    cells: Dict[str, Cell] = field(default_factory=dict)
    checks: List[Check] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        levels = [c.level for c in self.checks]
        for cell in self.cells.values():
            levels.extend(c.level for c in cell.checks)
        return max(levels, key=LEVELS.index, default="ok")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "status": self.status,
            "error": self.error,
            "checks": [_check(c) for c in self.checks],
            "cells": {key: _cell(cell) for key, cell in self.cells.items()},
        }


def _check(c: Check) -> Dict[str, str]:
    return {"name": c.name, "level": c.level, "message": c.message}


def _cell(cell: Cell) -> Dict[str, Any]:
    box = cell.box
    return {
        "value": _value(cell.value),
        "raw": cell.raw,
        "page": cell.page,
        "box": None if box is None else {"page": box.page, "x0": box.x0, "y0": box.y0, "x1": box.x1, "y1": box.y1},
        "source": cell.source,
        "edited": cell.edited,
        "checks": [_check(c) for c in cell.checks],
    }


def _value(value: Any) -> Any:
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value
