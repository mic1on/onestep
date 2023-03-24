# -*- coding: utf-8 -*-
from collections import defaultdict


class Event:

    def __init__(self):
        self._events = defaultdict(list)

    def on(self, event_name, fn):
        self._events[event_name].append(fn)

    def emit(self, event_name, **kwargs):
        for fn in self._events.get(event_name):
            fn(**kwargs)

    def remove(self, event_name, fn):
        self._events.get(event_name).remove(fn)

    def all(self):
        return self._events

    def __iadd__(self, other):
        combined_keys = self._events.keys() | other._events.keys()
        for key in combined_keys:
            self._events[key] += other._events.get(key, [])
        return self

    def __add__(self, other):
        return self.__iadd__(other)


event = Event()
