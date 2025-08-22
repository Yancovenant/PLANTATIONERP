# Part of Inphms, see License file for full copyright and licensing details.

from . import main

def __getattr__(attr):
    if attr != 'main':
        raise AttributeError(f"Module {__name__!r} has not attribute {attr!r}.")

    import sys  # noqa: PLC0415
    mod = __name__ + '.main'
    if main := sys.modules.get(mod):
        return main

    # can't use relative import as that triggers a getattr first
    import inphms.addons.web.controllers.main as main  # noqa: PLC0415
    return main