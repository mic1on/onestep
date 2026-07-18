from __future__ import annotations

import math
import re
from dataclasses import dataclass
from threading import Lock
from typing import Any, Literal


MetricKind = Literal["counter", "gauge"]

_METRIC_NAME_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_RESERVED_LABEL_NAMES = frozenset({"service", "environment", "task", "instance_id", "metric"})


@dataclass(frozen=True)
class CustomMetricSample:
    name: str
    kind: MetricKind
    value: int | float
    labels: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "value": self.value,
            "labels": dict(self.labels),
        }


class CustomMetricsRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._values: dict[tuple[str, str, MetricKind, tuple[tuple[str, str], ...]], float] = {}
        self._known_kinds: dict[tuple[str, str, tuple[tuple[str, str], ...]], MetricKind] = {}

    def for_task(self, task_name: str) -> "TaskMetrics":
        return TaskMetrics(self, task_name)

    def task_names(self) -> set[str]:
        with self._lock:
            return {key[0] for key in self._values}

    def increment_counter(
        self,
        *,
        task_name: str,
        name: str,
        labels: dict[str, str],
        amount: int | float,
    ) -> None:
        normalized_amount = _coerce_metric_value(amount)
        if normalized_amount < 0:
            raise ValueError("counter increment must be >= 0")
        key = self._series_key(
            task_name=task_name,
            name=name,
            kind="counter",
            labels=labels,
        )
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + normalized_amount

    def set_gauge(
        self,
        *,
        task_name: str,
        name: str,
        labels: dict[str, str],
        value: int | float,
    ) -> None:
        normalized_value = _coerce_metric_value(value)
        key = self._series_key(
            task_name=task_name,
            name=name,
            kind="gauge",
            labels=labels,
        )
        with self._lock:
            self._values[key] = normalized_value

    def rotate_task(self, task_name: str) -> list[dict[str, Any]]:
        samples: list[CustomMetricSample] = []
        with self._lock:
            for key, value in list(self._values.items()):
                series_task_name, name, kind, labels_tuple = key
                if series_task_name != task_name:
                    continue
                if kind == "counter":
                    if value == 0:
                        continue
                    self._values[key] = 0.0
                labels = dict(labels_tuple)
                samples.append(
                    CustomMetricSample(
                        name=name,
                        kind=kind,
                        value=_json_number(value),
                        labels=labels,
                    )
                )
        samples.sort(key=lambda sample: (sample.name, sample.kind, sorted(sample.labels.items())))
        return [sample.as_dict() for sample in samples]

    def _series_key(
        self,
        *,
        task_name: str,
        name: str,
        kind: MetricKind,
        labels: dict[str, str],
    ) -> tuple[str, str, MetricKind, tuple[tuple[str, str], ...]]:
        normalized_name = _normalize_metric_name(name)
        normalized_labels = _normalize_labels(labels)
        identity = (task_name, normalized_name, normalized_labels)
        with self._lock:
            existing_kind = self._known_kinds.get(identity)
            if existing_kind is not None and existing_kind != kind:
                raise ValueError(
                    f"custom metric {normalized_name!r} already exists as {existing_kind}"
                )
            self._known_kinds[identity] = kind
        return (task_name, normalized_name, kind, normalized_labels)


class TaskMetrics:
    def __init__(self, registry: CustomMetricsRegistry, task_name: str) -> None:
        self._registry = registry
        self._task_name = task_name

    def counter(self, name: str, *, labels: dict[str, Any] | None = None) -> "CounterMetric":
        return CounterMetric(
            self._registry,
            task_name=self._task_name,
            name=name,
            labels=_normalize_labels(labels or {}),
        )

    def gauge(self, name: str, *, labels: dict[str, Any] | None = None) -> "GaugeMetric":
        return GaugeMetric(
            self._registry,
            task_name=self._task_name,
            name=name,
            labels=_normalize_labels(labels or {}),
        )


class CounterMetric:
    def __init__(
        self,
        registry: CustomMetricsRegistry,
        *,
        task_name: str,
        name: str,
        labels: tuple[tuple[str, str], ...],
    ) -> None:
        self._registry = registry
        self._task_name = task_name
        self._name = name
        self._labels = dict(labels)

    def inc(self, amount: int | float = 1) -> None:
        self._registry.increment_counter(
            task_name=self._task_name,
            name=self._name,
            labels=self._labels,
            amount=amount,
        )


class GaugeMetric:
    def __init__(
        self,
        registry: CustomMetricsRegistry,
        *,
        task_name: str,
        name: str,
        labels: tuple[tuple[str, str], ...],
    ) -> None:
        self._registry = registry
        self._task_name = task_name
        self._name = name
        self._labels = dict(labels)

    def set(self, value: int | float) -> None:
        self._registry.set_gauge(
            task_name=self._task_name,
            name=self._name,
            labels=self._labels,
            value=value,
        )


def _normalize_metric_name(name: str) -> str:
    if not isinstance(name, str):
        raise TypeError("custom metric name must be a string")
    normalized = name.strip()
    if not normalized or not _METRIC_NAME_RE.match(normalized):
        raise ValueError(f"invalid custom metric name: {name!r}")
    return normalized


def _normalize_labels(labels: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    normalized: dict[str, str] = {}
    for raw_key, raw_value in labels.items():
        if not isinstance(raw_key, str):
            raise TypeError("custom metric label keys must be strings")
        key = raw_key.strip()
        if not key or not _LABEL_NAME_RE.match(key):
            raise ValueError(f"invalid custom metric label key: {raw_key!r}")
        if key in _RESERVED_LABEL_NAMES:
            raise ValueError(f"custom metric label {key!r} is reserved")
        normalized[key] = str(raw_value)
    return tuple(sorted(normalized.items()))


def _coerce_metric_value(value: int | float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("custom metric value must be an int or float")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError("custom metric value must be finite")
    return normalized


def _json_number(value: float) -> int | float:
    if value.is_integer():
        return int(value)
    return value


__all__ = [
    "CounterMetric",
    "CustomMetricSample",
    "CustomMetricsRegistry",
    "GaugeMetric",
    "TaskMetrics",
]
