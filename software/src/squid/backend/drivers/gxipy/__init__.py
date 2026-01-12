#!/usr/bin/python
# -*- coding:utf-8 -*-
# -*-mode:python ; tab-width:4 -*- ex:set tabstop=4 shiftwidth=4 expandtab: -*-

from squid.backend.drivers.gxipy import dxwrapper as _dxwrapper
from squid.backend.drivers.gxipy import gxiapi as _gxiapi
from squid.backend.drivers.gxipy import gxidef as _gxidef
from squid.backend.drivers.gxipy import gxwrapper as _gxwrapper

__all__ = ["gxwrapper", "dxwrapper", "gxiapi", "gxidef"]


def _export(module):
    names = getattr(module, "__all__", None)
    if names is None:
        names = [name for name in dir(module) if not name.startswith("_")]
    for name in names:
        globals()[name] = getattr(module, name)
    return names


__all__.extend(_export(_gxwrapper))
__all__.extend(_export(_dxwrapper))
__all__.extend(_export(_gxidef))
__all__.extend(_export(_gxiapi))

__version__ = "1.0.1809.9281"
