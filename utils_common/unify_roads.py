# -*- coding: utf-8 -*-
# vial_qgis_plugin/core/fuse_roads.py

from typing import List
import processing
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsCoordinateReferenceSystem,
    QgsApplication, QgsWkbTypes
)

# ----------------- Helpers internos -----------------

def _reproject_to(layer: QgsVectorLayer, crs: QgsCoordinateReferenceSystem) -> QgsVectorLayer:
    if layer.crs() == crs:
        return layer
    return processing.run(
        "native:reprojectlayer",
        {"INPUT": layer, "TARGET_CRS": crs, "OUTPUT": "TEMPORARY_OUTPUT"}
    )["OUTPUT"]

def _fix(layer: QgsVectorLayer) -> QgsVectorLayer:
    return processing.run(
        "native:fixgeometries",
        {"INPUT": layer, "OUTPUT": "TEMPORARY_OUTPUT"}
    )["OUTPUT"]

def _merge_layers(layers: List[QgsVectorLayer], crs: QgsCoordinateReferenceSystem) -> QgsVectorLayer:
    return processing.run(
        "native:mergevectorlayers",
        {"LAYERS": layers, "CRS": crs, "OUTPUT": "TEMPORARY_OUTPUT"}
    )["OUTPUT"]

def _linemerge_only(layer: QgsVectorLayer) -> QgsVectorLayer:
    for alg_id in ("native:linemerge", "qgis:linemerge"):
        if QgsApplication.processingRegistry().algorithmById(alg_id):
            try:
                return processing.run(alg_id, {"INPUT": layer, "OUTPUT": "TEMPORARY_OUTPUT"})["OUTPUT"]
            except Exception:
                pass
    return layer

def _drop_conflicting_id_fields(layer: QgsVectorLayer) -> QgsVectorLayer:
    try:
        existing = {f.name() for f in layer.fields()}
        to_drop = [c for c in ["fid","FID","ogc_fid","OGC_FID","objectid","OBJECTID"] if c in existing]
        if not to_drop:
            return layer
        for alg_id in ("qgis:deletecolumn", "native:deletecolumn"):
            if QgsApplication.processingRegistry().algorithmById(alg_id):
                return processing.run(
                    alg_id, {"INPUT": layer, "COLUMN": to_drop, "OUTPUT": "TEMPORARY_OUTPUT"}
                )["OUTPUT"]
    except Exception:
        pass
    return layer

# ----------------- API pública: fusión por prioridad -----------------

def fuse_any_layers_by_priority(
    layers_in_order: List[QgsVectorLayer],
    *,
    buffer_m: float = 10.0,
    post_clean: bool = False,
    show_debug: bool = False,
    final_name: str = "Malla_Vial_Final",
    add_to_project: bool = False,
) -> QgsVectorLayer:
    """
    Fusiona N capas de líneas por orden de prioridad.
    La primera capa tiene mayor prioridad. Para cada capa siguiente:
      1) se crea un buffer (corredor) sobre la acumulada,
      2) se recorta de la siguiente lo que caiga dentro del corredor,
      3) se une lo que quedó por fuera con la acumulada.
    """
    if not layers_in_order:
        raise ValueError("Debes proporcionar al menos una capa de líneas.")

    # Validación de tipos y CRS base
    for lyr in layers_in_order:
        if not isinstance(lyr, QgsVectorLayer) or not lyr.isValid():
            raise ValueError("Una de las capas no es válida.")
        if QgsWkbTypes.geometryType(lyr.wkbType()) != QgsWkbTypes.LineGeometry:
            raise ValueError(f"La capa '{lyr.name()}' no es de tipo línea.")

    crs = layers_in_order[0].crs()
    if not crs.isValid():
        raise ValueError("El CRS de la primera capa no es válido.")

    # Preparar en mismo CRS y corregir geometría
    prepared = []
    for lyr in layers_in_order:
        lyrp = _reproject_to(lyr, crs)
        lyrp = _fix(lyrp)
        prepared.append(lyrp)

    current = prepared[0]

    for nxt in prepared[1:]:
        corridor = processing.run(
            "native:buffer",
            {
                "INPUT": current,
                "DISTANCE": float(buffer_m),
                "SEGMENTS": 8,
                "END_CAP_STYLE": 1,
                "JOIN_STYLE": 1,
                "MITER_LIMIT": 2,
                "DISSOLVE": True,
                "OUTPUT": "TEMPORARY_OUTPUT",
            }
        )["OUTPUT"]

        nxt_outside = processing.run(
            "native:difference",
            {"INPUT": nxt, "OVERLAY": corridor, "OUTPUT": "TEMPORARY_OUTPUT"}
        )["OUTPUT"]

        current = _merge_layers([current, nxt_outside], crs)

    final = _linemerge_only(current) if post_clean else current
    final = _drop_conflicting_id_fields(final)
    final.setName(final_name)

    if add_to_project:
        QgsProject.instance().addMapLayer(final)
    return final
