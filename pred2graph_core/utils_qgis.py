# =============================
# File: utils_qgis.py (py39-safe)
# =============================
import os
import tempfile
from typing import Optional, Iterable, Tuple, Union

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsMessageLog,
    QgsProject,
    Qgis,
    QgsMapLayer,
    QgsVectorLayer,
    QgsWkbTypes,
    QgsRectangle,
    QgsRasterLayer,
)

PLUGIN_LOG = "Predial2Roads"

def msg_bar(iface, text: str, level=Qgis.Info, duration=6):
    try:
        QgsMessageLog.logMessage(text, PLUGIN_LOG, level)
        # Título de la barra en español para el usuario
        iface.messageBar().pushMessage("Predial → Malla Vial", text, level, duration)
    except Exception:
        pass

def _load_layer_from_path(path: str, name: Optional[str] = None) -> Optional[QgsMapLayer]:
    """
    Try to load a raster first; if not valid, try a vector via OGR.
    Works for GeoTIFF, NetCDF (GDAL raster), GPKG, SHP, etc.
    """
    if not path or not os.path.exists(path):
        return None

    # Try raster
    r = QgsRasterLayer(path, name or os.path.basename(path))
    if r.isValid():
        return r

    # Try vector (OGR)
    v = QgsVectorLayer(path, name or os.path.basename(path), "ogr")
    if v.isValid():
        return v

    return None

def add_tmp_layer(layer_or_path: Union[QgsMapLayer, str], name: Optional[str] = None):
    """
    Add a layer to the project. If given a file path, load it first.
    Supports rasters (tif/nc/…) and vectors (gpkg/shp/…).
    """
    lyr = None
    if isinstance(layer_or_path, QgsMapLayer):
        lyr = layer_or_path
        if name:
            try:
                lyr.setName(name)
            except Exception:
                pass
    elif isinstance(layer_or_path, str):
        lyr = _load_layer_from_path(layer_or_path, name)

    if not lyr:
        return

    try:
        QgsProject.instance().addMapLayer(lyr)
    except Exception:
        pass

def pick_selected_polygons(iface):
    try:
        layers = iface.layerTreeView().selectedLayers()
    except Exception:
        layers = []
    return [
        lyr for lyr in layers
        if isinstance(lyr, QgsVectorLayer) and lyr.isValid() and lyr.geometryType() == QgsWkbTypes.PolygonGeometry
    ]

def ensure_projected_crs(layer: QgsVectorLayer):
    crs: QgsCoordinateReferenceSystem = layer.crs()
    if crs.isGeographic():
        # Mensaje de error en español (se mostrará al usuario si ocurre)
        raise ValueError("El CRS de la capa Base Predial es geográfico (grados). Reproyecte a un CRS proyectado en metros antes de ejecutar.")

def same_crs(a: QgsVectorLayer, b: QgsVectorLayer) -> bool:
    try:
        return a.crs().authid() == b.crs().authid()
    except Exception:
        return False

def rect_to_extent_param(ext: QgsRectangle) -> str:
    # Processing expects "xmin,xmax,ymin,ymax"
    return f"{ext.xMinimum()},{ext.xMaximum()},{ext.yMinimum()},{ext.yMaximum()}"
