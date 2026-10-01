"""Таймфреймы платформы (ADR-0028): готовые свечи приходят от importer."""

from datetime import timedelta

# Код → номинальная длительность свечи; порядок — от младшего к старшему.
TIMEFRAMES: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
    "1w": timedelta(days=7),
}
