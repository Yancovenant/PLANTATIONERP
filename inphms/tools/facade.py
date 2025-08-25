import functools
import inspect

class ProxyMeta(type):
    def __new__(cls, clsname, bases, attrs):
        attrs.update({func: ProxyFunc() for func in ("__repr__", "__str__") if func not in attrs})
        proxy_class = super().__new__(cls, clsname, bases, attrs)
        # To preserve the docstring, signature, code of the wrapped class
        # `updated` to an emtpy list so it doesn't copy the `__dict__`
        # See `functools.WRAPPER_ASSIGNMENTS` and `functools.WRAPPER_UPDATES`
        functools.update_wrapper(proxy_class, proxy_class._wrapped__, updated=[])
        return proxy_class

class Proxy(metaclass=ProxyMeta):
    """
    A proxy class implementing the Facade pattern.

    This class delegates to an underlying instance while exposing a curated subset of its attributes and methods.
    Useful for controlling access, simplifying interfaces, or adding cross-cutting concerns.
    """
    _wrapped__ = object

    def __init__(self, instance):
        """
        Initializes the proxy by setting the wrapped instance.

        :param instance: The instance of the class to be wrapped.
        """
        object.__setattr__(self, "_wrapped__", instance)

    @property
    def __class__(self):
        return type(self)._wrapped__