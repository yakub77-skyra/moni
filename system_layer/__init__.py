"""System Layer package — style router for daily news reels.

One reference pack per job. Never load 2+ style specs into one composition.
"""

from system_layer.router import route_batch, route_news_item

__all__ = ["route_batch", "route_news_item"]
