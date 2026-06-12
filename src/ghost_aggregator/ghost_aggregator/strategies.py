"""Aggregation strategies: how N operator contributions on one channel
are weighted against each other.

A strategy only assigns per-operator weights; the aggregator node resolves
weights into the final command (weighted mean of active contributions, so a
one-hot weighting degenerates to exactly that operator's command). Strategies
are deliberately ROS-free so new ones can be unit-tested in isolation.
"""

from dataclasses import dataclass


@dataclass
class Contribution:
    """One operator's most recent input on a channel, as seen at fuse time."""
    operator_id: str
    twist: object  # geometry_msgs/Twist; strategies only read its fields
    age: float     # seconds since the input arrived at the aggregator
    active: bool   # False once older than the staleness window


class AggregationStrategy:
    def weights(self, contributions: list) -> dict:
        """Return {operator_id: weight in [0, 1]} for every contribution."""
        raise NotImplementedError


class Passthrough(AggregationStrategy):
    """Most recent active contribution wins outright (last-writer-wins).

    With a single operator this reproduces current single-client behavior,
    which makes it the integration baseline."""

    def weights(self, contributions):
        active = [c for c in contributions if c.active]
        newest = min(active, key=lambda c: c.age) if active else None
        return {c.operator_id: 1.0 if c is newest else 0.0 for c in contributions}


class ManualSelect(AggregationStrategy):
    """One-hot on an externally chosen operator (manual switching).

    Everyone else's input is received and logged but carries no weight."""

    def __init__(self, selected_operator: str):
        self.selected_operator = selected_operator

    def weights(self, contributions):
        return {
            c.operator_id: 1.0 if c.active and c.operator_id == self.selected_operator else 0.0
            for c in contributions
        }


class Mean(AggregationStrategy):
    """Every active operator weighted equally (uniform averaging)."""

    def weights(self, contributions):
        return {c.operator_id: 1.0 if c.active else 0.0 for c in contributions}


def make_strategy(name: str, selected_operator: str = "") -> AggregationStrategy:
    if name == "passthrough":
        return Passthrough()
    if name == "select":
        return ManualSelect(selected_operator)
    if name == "mean":
        return Mean()
    raise ValueError(f"unknown aggregation strategy: {name!r}")
