"""Phase 2 analytics (ADR 0013): reads the normalized layer, writes derived tables, never
reads raw payloads and never calls Garmin."""

from garminagent.analytics.step import ANALYTICS, AnalyticsResult, analyze, run_analytics

__all__ = ["ANALYTICS", "AnalyticsResult", "analyze", "run_analytics"]
