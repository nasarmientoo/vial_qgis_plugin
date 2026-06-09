# -*- coding: utf-8 -*-
from .plugin import AttrEditorPlugin

def classFactory(iface):
    """QGIS calls this to instantiate the plugin."""
    return AttrEditorPlugin(iface)
