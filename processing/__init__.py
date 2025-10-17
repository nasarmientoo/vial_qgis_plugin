# -*- coding: utf-8 -*-
def classFactory():
    # kept for completeness; QGIS discovers providers via plugin.py
    from .provider import VialProvider
    return VialProvider()
