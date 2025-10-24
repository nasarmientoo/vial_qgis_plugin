# =============================
# File: predial2roads.py
# =============================
from typing import Dict, Optional

from qgis.core import QgsVectorLayer, QgsRasterLayer, QgsProcessingException, QgsWkbTypes
from qgis import processing
from ..utils_common.road_chain_merger import merge_lines_to_dissolved
from .utils_qgis import ensure_projected_crs, same_crs, rect_to_extent_param

# --- helpers ---
def _fix(layer: QgsVectorLayer) -> QgsVectorLayer:
    return processing.run(
        "native:fixgeometries",
        {"INPUT": layer, "METHOD": 1, "OUTPUT": "TEMPORARY_OUTPUT"}
    )["OUTPUT"]

def _reproject_like(target_like: QgsVectorLayer, layer: QgsVectorLayer) -> QgsVectorLayer:
    """
    Reproyecta `layer` al mismo CRS de `target_like` si difieren.
    """
    if same_crs(target_like, layer):
        return layer
    return processing.run(
        "native:reprojectlayer",
        {"INPUT": layer, "TARGET_CRS": target_like.crs(), "OUTPUT": "TEMPORARY_OUTPUT"}
    )["OUTPUT"]

def _dissolve_all(layer: QgsVectorLayer) -> QgsVectorLayer:
    """
    Disolver todo en una sola geometría (cuando aplica).
    """
    return processing.run(
        "native:dissolve",
        {"INPUT": layer, "OUTPUT": "TEMPORARY_OUTPUT"}
    )["OUTPUT"]

def _buffer(layer: QgsVectorLayer, dist: float) -> QgsVectorLayer:
    """
    Buffer/disolución con distancia `dist` (m) para construir el AOI.
    """
    return processing.run(
        "native:buffer",
        {
            "INPUT": layer, "DISTANCE": float(dist),
            "SEGMENTS": 8, "END_CAP_STYLE": 0, "JOIN_STYLE": 0, "MITER_LIMIT": 2,
            "DISSOLVE": True, "OUTPUT": "TEMPORARY_OUTPUT"
        }
    )["OUTPUT"]

def _difference(a: QgsVectorLayer, b: QgsVectorLayer) -> QgsVectorLayer:
    """
    Diferencia espacial A \ B.
    """
    return processing.run(
        "native:difference",
        {"INPUT": a, "OVERLAY": b, "OUTPUT": "TEMPORARY_OUTPUT"}
    )["OUTPUT"]

def _multipart_to_single(layer: QgsVectorLayer) -> QgsVectorLayer:
    """
    Convierte geometrías multiparte a geometrías de una sola parte.
    """
    return processing.run(
        "native:multiparttosingleparts",
        {"INPUT": layer, "OUTPUT": "TEMPORARY_OUTPUT"}
    )["OUTPUT"]

def _extract_by_expr(layer: QgsVectorLayer, expr: str) -> QgsVectorLayer:
    """
    Extrae elementos que cumplan la expresión QGIS `expr`.
    """
    return processing.run(
        "native:extractbyexpression",
        {"INPUT": layer, "EXPRESSION": expr, "OUTPUT": "TEMPORARY_OUTPUT"}
    )["OUTPUT"]

def _delete_dupes(layer: QgsVectorLayer) -> QgsVectorLayer:
    """
    Elimina geometrías duplicadas.
    """
    return processing.run(
        "native:deleteduplicategeometries",
        {"INPUT": layer, "OUTPUT": "TEMPORARY_OUTPUT"}
    )["OUTPUT"]

def _rasterize_vector(polys: QgsVectorLayer, extent_src: QgsVectorLayer, res_m: float) -> str:
    """
    Escribe un GeoTIFF real (no TEMPORARY_OUTPUT) para que GRASS/GDAL lo encuentren después.
    Fondo (no-vacío) = 0, Frente (vacíos) = 1.
    Devuelve la ruta del archivo (string).
    """
    import os, tempfile
    out_tif = os.path.join(tempfile.gettempdir(), "voids_rast.tif")
    res = processing.run(
        "gdal:rasterize",
        {
            "INPUT": polys,
            "FIELD": None,
            "BURN": 1,
            "UNITS": 1,                       # unidades georreferenciadas
            "WIDTH": float(res_m),            # tamaño de píxel X
            "HEIGHT": float(res_m),           # tamaño de píxel Y
            "EXTENT": rect_to_extent_param(extent_src.extent()),
            "NODATA": 0,                      # fondo etiquetado como 0
            "DATA_TYPE": 1,                   # Byte
            "INIT": 0,                        # inicializa fondo a 0
            "INVERT": False,
            "EXTRA": "-co COMPRESS=LZW -co TILED=YES",
            "OUTPUT": out_tif                 # <- archivo real y estable
        }
    )
    return res["OUTPUT"]  # ruta a out_tif


def _ensure_byte_raster(in_raster_path: str) -> str:
    """
    Garantiza que el ráster sea de tipo Byte y persiste en disco (no TEMPORARY_OUTPUT).
    """
    import os, tempfile
    out_tif = os.path.join(tempfile.gettempdir(), "voids_rast_byte.tif")
    res = processing.run(
        "gdal:translate",
        {
            "INPUT": in_raster_path,
            "TARGET_CRS": None,
            "NODATA": 0,
            "COPY_SUBDATASETS": False,
            "OPTIONS": "",
            "DATA_TYPE": 1,              # Byte
            "EXTRA": "-co COMPRESS=LZW -co TILED=YES",
            "OUTPUT": out_tif            # <- archivo real y estable
        }
    )
    return res["OUTPUT"]  # ruta a out_tif


def _grass_bin(in_raster_path: str) -> str:
    """
    Convierte ráster 0/1 a 1/NULL (el fondo en GRASS debe ser NULL para r.thin).
    Devuelve la ruta del ráster producido por GRASS (a menudo NetCDF).
    """
    return processing.run(
        "grass7:r.mapcalc.simple",
        {
            "a": in_raster_path,
            "expression": "if(A>0,1,null())",
            "output": "TEMPORARY_OUTPUT"
        }
    )["output"]

def _grass_r_thin(in_raster_path: str, iterations: int = 500) -> str:
    """
    Adelgazamiento (skeletonization) con GRASS r.thin.
    """
    return processing.run(
        "grass7:r.thin",
        {
            "input": in_raster_path,
            "iterations": int(iterations),
            "output": "TEMPORARY_OUTPUT"
        }
    )["output"]

def _grass_r_to_vect_lines(thin_rast_path: str) -> str:
    """
    Convierte el ráster adelgazado (1/NULL) a vectores de LÍNEA de forma robusta
    para diferentes compilaciones de QGIS/GRASS.
    """
    return processing.run(
        "grass7:r.to.vect",
        {
            "input": thin_rast_path,
            "type": 0,    # 0: líneas, 1: puntos, 2: polígonos
            "column": "value",
            "output": "TEMPORARY_OUTPUT",
        }
    )["output"]

# --- ayuda: conservar solo polígonos "reales" (a veces se crean vacíos) ---
def _filter_to_real_polygons(layer: QgsVectorLayer, min_area_m2: float = 1.0) -> QgsVectorLayer:
    """
    Filtra a verdaderos polígonos (no nulos) con área mínima `min_area_m2`.
    """
    lyr = _multipart_to_single(_fix(layer))
    lyr = _extract_by_expr(
        lyr,
        f"$geometry IS NOT NULL AND geometry_type($geometry) LIKE 'Polygon%' AND $area >= {float(min_area_m2)}"
    )
    lyr = _delete_dupes(lyr)
    return lyr

def _line_intersections(lines: QgsVectorLayer) -> QgsVectorLayer:
    """
    Calcula puntos de intersección de líneas (autointersección: la capa consigo misma).
    """
    return processing.run(
        "native:lineintersections",
        {
            "INPUT": lines,
            "INTERSECT": lines,
            "INPUT_FIELDS": [],
            "INTERSECT_FIELDS": [],
            "INPUT_FIELDS_PREFIX": "",
            "INTERSECT_FIELDS_PREFIX": "",
            "OUTPUT": "TEMPORARY_OUTPUT"
        }
    )["OUTPUT"]

def _join_intersection_counts(lines: QgsVectorLayer, points: QgsVectorLayer) -> QgsVectorLayer:
    """
    Une por localización (resumen) para obtener la cuenta de puntos de intersección por línea.
    Distintas versiones de QGIS pueden nombrar el campo de cuenta de forma diferente;
    normalizamos a un nuevo campo 'value_count' para etapas posteriores.
    """
    joined = processing.run(
        "native:joinbylocationsummary",
        {
            "INPUT": lines,
            "JOIN": points,
            "PREDICATE": [0],                # 0: intersects
            "JOIN_FIELDS": [],               # sin estadísticas por campo, solo conteo
            "SUMMARIES": [5],                # 5: count
            "DISCARD_NONMATCHING": False,
            "OUTPUT": "TEMPORARY_OUTPUT"
        }
    )["OUTPUT"]

    # Normalizar el nombre del campo de conteo -> asegurar 'value_count'
    # Usamos coalesce sobre varios nombres comunes.
    norm = processing.run(
        "native:fieldcalculator",
        {
            "INPUT": joined,
            "FIELD_NAME": "value_count",
            "FIELD_TYPE": 1,     # 0=Float, 1=Integer, 2=String, 3=Date, ...
            "FIELD_LENGTH": 10,
            "FIELD_PRECISION": 0,
            "FORMULA": "coalesce(\"value_count\",\"count\",\"intersects_count\",\"join_count\")",
            "OUTPUT": "TEMPORARY_OUTPUT"
        }
    )["OUTPUT"]

    # Añadir longitud en metros como 'length_m'
    with_len = processing.run(
        "native:fieldcalculator",
        {
            "INPUT": norm,
            "FIELD_NAME": "length_m",
            "FIELD_TYPE": 0,     # Float
            "FIELD_LENGTH": 20,
            "FIELD_PRECISION": 3,
            "FORMULA": "$length",   # CRS ya forzado a proyectado en metros anteriormente
            "OUTPUT": "TEMPORARY_OUTPUT"
        }
    )["OUTPUT"]

    return with_len

def _extract_dangles(roads_with_counts: QgsVectorLayer) -> QgsVectorLayer:
    """
    Define dangles como (value_sum <= 6 O value_sum ES NULL) Y length_m < 20.
    """
    expr = "(\"value_sum\" <= 6 OR \"value_sum\" IS NULL) AND \"length_m\" < 20"
    return processing.run(
        "native:extractbyexpression",
        {"INPUT": roads_with_counts, "EXPRESSION": expr, "OUTPUT": "TEMPORARY_OUTPUT"}
    )["OUTPUT"]

def _remove_dangles(roads_with_counts: QgsVectorLayer) -> QgsVectorLayer:
    """
    Mantener solo los NO-dangles (negación de la expresión de dangles).
    """
    keep_expr = "NOT ((\"value_sum\" <= 6 OR \"value_sum\" IS NULL) AND \"length_m\" < 20)"
    return processing.run(
        "native:extractbyexpression",
        {"INPUT": roads_with_counts, "EXPRESSION": keep_expr, "OUTPUT": "TEMPORARY_OUTPUT"}
    )["OUTPUT"]

def _simplify_lines(lines: QgsVectorLayer, tolerance_m: float) -> QgsVectorLayer:
    """
    Simplifica geometrías para líneas más limpias. Método Douglas-Peucker con
    tolerancia en metros.
    """
    return processing.run(
        "native:simplifygeometries",
        {
            "INPUT": lines,
            "METHOD": 0,             # 0 = Douglas-Peucker
            "TOLERANCE": float(tolerance_m),
            "OUTPUT": "TEMPORARY_OUTPUT"
        }
    )["OUTPUT"]

# ---------- PRINCIPAL: pasos 1→10 ----------

def run_steps_1_to_10(
    base_predial: QgsVectorLayer,
    boundaries: QgsVectorLayer,
    buffer_m: float = 40.0,
    raster_res_m: float = 1.0,
    thin_iterations: int = 500,
    simplify_tolerance_m: float = 1.0,
) -> Dict[str, object]:
    """
    Devuelve un diccionario con las salidas:
      (1–7)
      - aoi
      - voids
      - voids_rast
      - voids_rast_byte
      - voids_rast_bin
      - roads_thin
      - roads_lines

      (8–10)
      - road_intersections
      - roads_with_counts
      - roads_dangles
      - roads_clean
      - roads_simplified
    """
    outputs: Dict[str, object] = {}

    # ----- Paso 1: Comprobar/Reproyectar CRS: Boundaries -> CRS de Base Predial -----
    base_predial = _fix(base_predial)
    boundaries = _reproject_like(base_predial, boundaries)

    # Asegurar que los polígonos base sean válidos antes del disolver
    base_clean = _filter_to_real_polygons(base_predial, min_area_m2=1.0)

    # ----- Paso 2a: Disolver Base Predial (tejido urbano) -----
    fabric = _dissolve_all(base_clean)

    # ----- Paso 2b: AOI = Buffer(fabric, buffer_m) -----
    aoi = _buffer(fabric, buffer_m)
    aoi = _filter_to_real_polygons(aoi, min_area_m2=1.0)
    outputs["aoi"] = aoi

    # ----- Paso 3: Vacíos = Diferencia(AOI tamponado, Base Predial) y limpiar -----
    voids_raw = _difference(aoi, base_clean)
    voids = _filter_to_real_polygons(voids_raw, min_area_m2=1.0)
    outputs["voids"] = voids

    # ----- Paso 4: Rasterizar vacíos (1 m) usando la extensión del AOI -----
    voids_for_rast = _dissolve_all(voids)
    voids_rast = _rasterize_vector(voids_for_rast, aoi, raster_res_m)
    outputs["voids_rast"] = voids_rast

    # ----- Paso 5: Asegurar que el ráster sea Byte -----
    voids_rast_byte = _ensure_byte_raster(voids_rast)
    outputs["voids_rast_byte"] = voids_rast_byte

    # ----- Paso 6: Binarizar 1/NULL y adelgazar (GRASS r.thin) -----
    voids_rast_bin = _grass_bin(voids_rast_byte)
    outputs["voids_rast_bin"] = voids_rast_bin

    roads_thin = _grass_r_thin(voids_rast_bin, iterations=thin_iterations)
    outputs["roads_thin"] = roads_thin

    # ----- Paso 7: Convertir a líneas (tipo "line") -----
    roads_lines = _grass_r_to_vect_lines(roads_thin)
    outputs["roads_lines"] = roads_lines

    # ----- Paso 8: Identificar dangles -----
    # 8.1 Intersecciones (puntos)
    road_intersections = _line_intersections(roads_lines)
    outputs["road_intersections"] = road_intersections

    # 8.2 Unir conteo de intersecciones por línea + length_m
    roads_with_counts = _join_intersection_counts(roads_lines, road_intersections)
    outputs["roads_with_counts"] = roads_with_counts

    # 8.3 Extraer dangles por expresión
    roads_dangles = _extract_dangles(roads_with_counts)
    outputs["roads_dangles"] = roads_dangles

    # ----- Paso 9: Eliminar dangles (conservar solo no-dangles) -----
    roads_clean = _remove_dangles(roads_with_counts)
    outputs["roads_clean"] = roads_clean

    # ----- Paso 10: Simplificar (suavizar) -----
    roads_simplified = _simplify_lines(roads_clean, tolerance_m=simplify_tolerance_m)
    outputs["roads_simplified"] = roads_simplified

    return outputs

def run_predial2roads(
    vector_layer: QgsVectorLayer,
    boundaries_layer: Optional[QgsVectorLayer] = None,
    raster_layer: Optional[QgsRasterLayer] = None,   # aceptado por compatibilidad; no se usa aquí
    feedback=None,
) -> Dict[str, object]:
    """
    Entry point llamado por alg_tool_one (casos 2 y 3).

    Flujo:
      1) Ejecuta run_steps_1_to_10 (AOI→Vacíos→Raster→Thin→Vector→Limpieza→Simplificar).
      2) Aplica MERGE de segmentos alineados → capa final disuelta ('Predial_calles_union').
      3) Devuelve todas las salidas + alias 'roads' → capa final MERGED (compatibilidad con alg_tool_one).
    """
    if vector_layer is None or not vector_layer.isValid():
        raise QgsProcessingException("Capa de predios inválida o no proporcionada.")

    # 1) Validación CRS proyectado
    try:
        ensure_projected_crs(vector_layer)
    except Exception as e:
        raise QgsProcessingException(str(e))

    # 2) Boundaries opcional (si no viene, usamos vector como ancla de CRS)
    _boundaries = boundaries_layer if (boundaries_layer and boundaries_layer.isValid()) else vector_layer

    if feedback:
        feedback.pushInfo("[Predial→Roads] Ejecutando flujo (AOI → Vacíos → Rasterizar → Adelgazado → Vectorizar → Limpieza → Simplificar)…")

    # 3) Pipeline base
    outs = run_steps_1_to_10(
        base_predial=vector_layer,
        boundaries=_boundaries,
        buffer_m=40.0,
        raster_res_m=1.0,
        thin_iterations=500,
        simplify_tolerance_m=1.0,
    )

    # 4) Tomar la capa simplificada (líneas) para el merge
    roads_simplified = outs.get("roads_simplified")
    if not isinstance(roads_simplified, QgsVectorLayer) or not roads_simplified.isValid():
        raise QgsProcessingException("'roads_simplified' no es una capa válida.")
    if roads_simplified.geometryType() != QgsWkbTypes.LineGeometry:
        raise QgsProcessingException("'roads_simplified' no es de tipo línea.")

    # 5) MERGE de segmentos alineados
    try:
        if feedback:
            feedback.pushInfo("[Predial→Roads] Uniendo segmentos alineados (chain-merge)…")
        final_layer = merge_lines_to_dissolved(
            roads_simplified,
            snap_tol_m=1.0,        # ajusta si el dataset lo requiere
            angle_thresh_deg=15.0, # ajusta si necesitas más/menos estricto
            final_name="Predial_calles_union",
        )
    except Exception as e:
        # Si el merge falla, devolvemos la capa simplificada para no romper el flujo
        if feedback:
            feedback.pushWarning(f"[Predial→Roads] Merge falló: {e}. Se devuelve 'roads_simplified'.")
        final_layer = roads_simplified

    # 6) Exponer la salida principal como 'roads' (compatibilidad con alg_tool_one)
    outs["roads_merged"] = final_layer
    outs["roads"] = final_layer  # alias que usa alg_tool_one

    if feedback:
        feedback.pushInfo("[Predial→Roads] Listo: capas 'roads_lines', 'roads_simplified' y 'Predial_calles_union' producidas.")

    return outs