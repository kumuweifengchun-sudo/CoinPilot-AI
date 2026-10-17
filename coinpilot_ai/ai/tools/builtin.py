from typing import TYPE_CHECKING

from .account import AccountTools
from .market import MarketTools
from .indicators import IndicatorTools
from .order import definition as order_definition
from .registry import ToolRegistry
from .review import ReviewTools
from .risk import RiskTools

if TYPE_CHECKING:
    from coinpilot_ai.application.service import CockpitService


def build_registry(service: "CockpitService") -> ToolRegistry:
    registry = ToolRegistry(lambda: service.ai_chat.scope if service.ai_chat is not None else service.scope, service)
    definitions = (MarketTools(service).definitions() + IndicatorTools(service).definitions() + AccountTools(service).definitions()
                   + ReviewTools(service).definitions() + RiskTools(service).definitions() + [order_definition()])
    for definition in definitions:
        registry.register(definition)
    return registry
