import warnings
warnings.warn(
    f"{__name__!r} Please use Home instead."
    "empty, all controllers and utility functions were moved to sibling "
    "submodules in Inphms",
    DeprecationWarning,
    stacklevel=2,
)