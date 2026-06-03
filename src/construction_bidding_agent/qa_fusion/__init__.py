"""Thin adapter for platform assistant Q&A."""

from .response_builder import build_platform_assistant_response
from .schemas import PlatformAssistantContext

__all__ = ["PlatformAssistantContext", "build_platform_assistant_response"]

