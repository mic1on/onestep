# -*- coding: utf-8 -*-
import typing


class State:
    _state: typing.Dict[str, typing.Any]

    def __init__(self, state: typing.Optional[typing.Dict[str, typing.Any]] = None):
        if state is None:
            state = {}
        super().__setattr__("_state", state)

    def __setattr__(self, key: typing.Any, value: typing.Any) -> None:
        self._state[key] = value

    def __getattr__(self, key: typing.Any) -> typing.Any:
        try:
            return self._state[key]
        except KeyError:
            return None

    def __delattr__(self, key: typing.Any) -> None:
        del self._state[key]
