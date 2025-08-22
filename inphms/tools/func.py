# Part of Inphms, see License file for full copyright and licensing details.

from __future__ import annotations
import typing
from inspect import Parameter, getsourcefile, signature

from decorator import decorator

__all__ = [
    # 'classproperty',
    # 'conditional',
    # 'lazy',
    # 'lazy_classproperty',
    # 'lazy_property',
    'synchronized',
]

T = typing.TypeVar("T")

if typing.TYPE_CHECKING:
    from collections.abc import Callable


def synchronized(lock_attr: str = '_lock'):
    @decorator
    def locked(func, inst, *args, **kwargs):
        with getattr(inst, lock_attr):
            return func(inst, *args, **kwargs)
    return locked
locked = synchronized()