"""Visual chart orders (draggable bracket orders on /trading).

The public surface is the engine singleton and the resolver. Everything below
it is pure or a thin dispatch layer, so the blueprint does not reach into the
database or the broker modules directly.
"""

from services.visual_bracket.engine import (
    VisualBracketEngine,
    get_visual_bracket_engine,
    start_visual_bracket_engine,
    stop_visual_bracket_engine,
)
from services.visual_bracket.resolver import (
    SUPPORTED_UNDERLYINGS,
    VisualBracketError,
    resolve_contract,
)

__all__ = [
    "SUPPORTED_UNDERLYINGS",
    "VisualBracketEngine",
    "VisualBracketError",
    "get_visual_bracket_engine",
    "resolve_contract",
    "start_visual_bracket_engine",
    "stop_visual_bracket_engine",
]
