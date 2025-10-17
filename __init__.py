# -*- coding: utf-8 -*-
from .plugin import VialPlugin

def classFactory(iface):
    """QGIS calls this to instantiate the plugin."""
    return VialPlugin(iface)