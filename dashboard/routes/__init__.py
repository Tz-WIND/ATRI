"""Dashboard API routes.

Each sub-module registers its routes on the Dashboard app via ``register(dashboard)``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dashboard.server import Dashboard


def register_all(dashboard: Dashboard) -> None:
    """Register every route group on *dashboard*."""
    from dashboard.routes import auth, chat, management, mcp, models, skills, websocket

    auth.register(dashboard)
    models.register(dashboard)
    chat.register(dashboard)
    mcp.register(dashboard)
    skills.register(dashboard)
    management.register(dashboard)
    websocket.register(dashboard)
