# =============================
# File: utils_qgis.py  (QGIS 3 / PyQt5/6, Python 3.9+)
# =============================
import json
import os
import tempfile
from typing import Optional, Tuple, Union, Callable

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsRasterLayer,
    QgsRectangle,
    QgsVectorLayer,
    QgsProcessingContext,
    QgsProcessingFeedback,
)
from qgis import processing

# ---------------- Sat2Graph docker automation ----------------
# Asegúrate de que docker_automation.py esté en el mismo paquete (sat2graph_core)
try:
    from .docker_automation import Sat2GraphDockerManager
    _HAS_DOCKER_AUTOMATION = True
except Exception as _e:
    Sat2GraphDockerManager = None  # type: ignore
    _HAS_DOCKER_AUTOMATION = False
# -------------------------------------------------------------


# ---------------------------------------------------------------------
# Exporta un subset del raster a GeoTIFF temporal (en CRS métrico)
# ---------------------------------------------------------------------
def export_raster_tile(layer: QgsRasterLayer, extent: Optional[QgsRectangle]) -> Tuple[str, float]:
    """
    Exporta (opcionalmente recorta) un raster georreferenciado a GeoTIFF temporal.
    Retorna (out_path, gsd_meters_per_px). Asume CRS en metros.
    """
    if not isinstance(layer, QgsRasterLayer) or not layer.isValid():
        raise ValueError("Invalid raster layer")

    crs: QgsCoordinateReferenceSystem = layer.crs()
    if crs.isGeographic():
        raise ValueError("Raster CRS is geographic. Reproject to a metric CRS before running.")

    prov = layer.dataProvider()
    ext = prov.extent()
    width = layer.width()
    height = layer.height()
    pix_x = ext.width() / width
    pix_y = abs(ext.height() / height)
    gsd = float((pix_x + pix_y) / 2.0)

    if extent is None:
        extent = ext

    out_tif = os.path.join(tempfile.gettempdir(), next(tempfile._get_candidate_names()) + ".tif")

    params = {
        "INPUT": layer.source(),
        "PROJWIN": extent,          # QgsRectangle aceptado por processing
        "NODATA": None,
        "TARGET_CRS": crs.toWkt(),  # mantener CRS
        "DATA_TYPE": 0,             # keep
        "OUTPUT": out_tif,
    }
    res = processing.run("gdal:translate", params)
    out_path = res["OUTPUT"] if res and "OUTPUT" in res else out_tif
    return out_path, gsd


# ---------------------------------------------------------------------
# Construcción de transformaciones pixel -> coordenadas de mapa
# ---------------------------------------------------------------------
def _build_px2map_from_geotransform(rlayer: QgsRasterLayer):
    """
    Intenta construir una función pixel->map usando el geotransform GDAL.
    Retorna callable (x_px, y_px) -> (X_map, Y_map), o None si falla.
    """
    try:
        prov = rlayer.dataProvider()
        gt = prov.geoTransform()  # (originX, pixelWidth, rotX, originY, rotY, pixelHeight)
        if gt and len(gt) == 6:
            def px2map(xp: float, yp: float) -> Tuple[float, float]:
                X = gt[0] + xp * gt[1] + yp * gt[2]
                Y = gt[3] + xp * gt[4] + yp * gt[5]
                return (X, Y)
            return px2map
    except Exception:
        pass
    return None


def _build_px2map_from_extent(rlayer: QgsRasterLayer):
    """
    Fallback north-up a partir de extent/size (sin rotación).
    """
    prov = rlayer.dataProvider()
    ext = prov.extent()
    width = rlayer.width()
    height = rlayer.height()

    # Tamaños de pixel; Y negativo porque el índice de fila crece hacia abajo
    px = ext.width() / width
    py = -ext.height() / height
    origin_x = ext.xMinimum()
    origin_y = ext.yMaximum()  # top-left Y

    def px2map(xp: float, yp: float) -> Tuple[float, float]:
        X = origin_x + xp * px
        Y = origin_y + yp * py
        return (X, Y)

    return px2map


# ---------------------------------------------------------------------
# Convierte edges JSON (en pixel) a capa LineString en CRS del raster
# ---------------------------------------------------------------------
def json_edges_to_layer(
    json_path: str,
    crs: QgsCoordinateReferenceSystem,
    geotransform_from_layer: Union[QgsRasterLayer, str],
) -> QgsVectorLayer:
    """
    Convierte edges de Sat2Graph (coordenadas de pixel) en una capa LineString en memoria.

    Formatos soportados:
      - Modelo 1: [ [[x1,y1],[x2,y2]], ... ]
      - Modelo 3: [ [[x1,y1],[x2,y2], weight], ... ]  (peso a atributo)

    Se lee el geotransform del raster de referencia para mapear pixel → mapa.

    Nota:
      - Se asume que el JSON puede venir como (row, col)=(y, x). Se realiza swap.
    """
    # 1) Carga edges
    with open(json_path, "r", encoding="utf-8") as f:
        edges = json.load(f)

    # 2) Raster de referencia
    if isinstance(geotransform_from_layer, QgsRasterLayer):
        rlayer = geotransform_from_layer
    else:
        rlayer = QgsRasterLayer(str(geotransform_from_layer), "_tmp_ref_", "gdal")
    if not rlayer.isValid():
        raise ValueError("Reference raster for geotransform is invalid.")

    # 3) Geotransform preferente; fallback norte-arriba
    px2map = _build_px2map_from_geotransform(rlayer) or _build_px2map_from_extent(rlayer)

    # 4) Capa en memoria
    vlayer = QgsVectorLayer("LineString?crs=" + crs.toWkt(), "Sat2Graph Roads", "memory")
    pr = vlayer.dataProvider()

    fields = QgsFields()
    fields.append(QgsField("id", QVariant.Int))
    fields.append(QgsField("weight", QVariant.Double))  # 0.0 si no viene
    pr.addAttributes(fields)
    vlayer.updateFields()

    # 5) Features
    feats = []
    for fid, edge in enumerate(edges):
        try:
            if isinstance(edge, (list, tuple)) and len(edge) >= 2:
                p1 = edge[0]
                p2 = edge[1]
                weight = None
                if len(edge) >= 3:
                    weight = edge[2]
            else:
                continue

            # JSON puede venir como (x,y) = (col,row) o al revés.
            # Aquí asumimos (row, col)=(y, x) → intercambiamos a (col,row)
            x1, y1 = float(p1[0]), float(p1[1])
            x2, y2 = float(p2[0]), float(p2[1])
            c1, r1 = y1, x1
            c2, r2 = y2, x2

            p1x, p1y = px2map(c1, r1)
            p2x, p2y = px2map(c2, r2)

            geom = QgsGeometry.fromPolylineXY([QgsPointXY(p1x, p1y), QgsPointXY(p2x, p2y)])
            feat = QgsFeature()
            feat.setGeometry(geom)
            feat.setAttributes([fid, float(weight) if weight is not None else 0.0])
            feats.append(feat)
        except Exception:
            # Ignorar edges malformados
            continue

    pr.addFeatures(feats)
    vlayer.updateExtents()
    return vlayer


# ---------------------------------------------------------------------
# Helpers para conectar feedback de QGIS al manager docker
# ---------------------------------------------------------------------
def _feedback_callback(feedback: QgsProcessingFeedback) -> Callable[[str], None]:
    """
    Convierte mensajes del manager docker a feedback de QGIS.
    """
    def _cb(msg: str):
        try:
            if isinstance(feedback, QgsProcessingFeedback):
                feedback.pushInfo(str(msg))
            else:
                print(str(msg))
        except Exception:
            print(str(msg))
    return _cb


# ---------------------------------------------------------------------
# Puente usado por el algoritmo de Processing (Tool 1)
# ---------------------------------------------------------------------
def run_sat2graph_raster(
    raster_layer: QgsRasterLayer,
    context: Optional[QgsProcessingContext] = None,
    feedback: Optional[QgsProcessingFeedback] = None,
    *,
    extent: Optional[QgsRectangle] = None,
    model_id: int = 3,
    cleanup: bool = True,
) -> QgsVectorLayer:
    """
    Entrada única para Tool 1 (Processing). Debe devolver SIEMPRE un QgsVectorLayer válido.

    Flujo:
      1) Exportar tile del raster (GeoTIFF temporal).
      2) Inicializar/arrancar contenedor Sat2Graph.
      3) Enviar tile a la API → obtener JSON de edges.
      4) Convertir JSON a capa vectorial en CRS del raster.
    """
    # Feedback dummy si no viene
    if feedback is None:
        class _Dummy:
            def pushInfo(self, m): print(m)
            def pushWarning(self, m): print("WARN:", m)
            def pushDebugInfo(self, m): print("DEBUG:", m)
            def reportError(self, m, fatal=False): print("ERROR:", m)
        feedback = _Dummy()  # type: ignore

    if raster_layer is None or not raster_layer.isValid():
        raise ValueError("Raster layer inválido.")

    # 1) Exportar tile temporal
    try:
        out_tif, gsd = export_raster_tile(raster_layer, extent)
        feedback.pushInfo(f"[Vial] Tile exportado: {out_tif} | GSD≈{gsd:.3f} m/px")
    except Exception as e:
        feedback.reportError(f"[Vial] Error exportando tile: {e}", fatal=True)
        # devolver capa vacía para no romper Processing
        v_stub = QgsVectorLayer(f"LineString?crs={raster_layer.crs().authid()}", "Vial_Graph", "memory")
        v_stub.updateExtents()
        return v_stub

    json_path = None

    # 2) Docker pipeline
    if not _HAS_DOCKER_AUTOMATION or Sat2GraphDockerManager is None:
        feedback.pushWarning("[Vial] docker_automation.py no disponible. Devuelvo capa vacía.")
    else:
        mgr = Sat2GraphDockerManager()
        mgr.add_status_callback(_feedback_callback(feedback))

        # 2.1 Docker up?
        if not mgr.initialize_docker():
            feedback.pushWarning("[Vial] Docker no está disponible. Instala/arranca Docker Desktop.")
        else:
            # 2.2 ¿Servidor ya listo?
            ready = mgr.is_container_running()
            if not ready:
                # 2.3 Pull si falta imagen y arranque
                if not mgr.pull_image():
                    feedback.pushWarning("[Vial] No se pudo descargar la imagen Docker.")
                elif not mgr.start_container():
                    feedback.pushWarning("[Vial] No se pudo iniciar el contenedor Sat2Graph.")
                else:
                    ready = True

            # 2.4 Inferencia
            if ready:
                try:
                    json_path = mgr.extract_roads(out_tif, gsd=gsd, model_id=model_id, allow_retry=True)
                except Exception as e:
                    feedback.pushWarning(f"[Vial] Error durante la inferencia: {e}")

    # 3) Construcción de capa de salida
    try:
        if json_path and os.path.exists(json_path):
            feedback.pushInfo(f"[Vial] Leyendo edges desde: {json_path}")
            vlayer = json_edges_to_layer(json_path, raster_layer.crs(), raster_layer)
        else:
            feedback.pushWarning("[Vial] No se obtuvo JSON de aristas; se devolverá capa vacía.")
            vlayer = QgsVectorLayer(f"LineString?crs={raster_layer.crs().authid()}", "Vial_Graph", "memory")
            vlayer.updateExtents()
    except Exception as e:
        feedback.reportError(f"[Vial] Error construyendo la capa de salida: {e}")
        vlayer = QgsVectorLayer(f"LineString?crs={raster_layer.crs().authid()}", "Vial_Graph", "memory")
        vlayer.updateExtents()

    # 4) Limpieza de temporales
    try:
        if cleanup:
            if os.path.isfile(out_tif):
                os.remove(out_tif)
            # Si quieres limpiar el JSON generado:
            # if json_path and os.path.isfile(json_path):
            #     os.remove(json_path)
    except Exception:
        pass

    return vlayer


def run_sat2graph_raster_vector(raster_layer, vector_layer, context, feedback):
    # TODO: implement your combined flow (e.g., mask by vector, clip to extent, etc.)
    raise NotImplementedError("Implement raster+vector path")

def run_sat2graph_vector_only(vector_layer, context, feedback):
    # TODO: implement vector-only behavior
    raise NotImplementedError("Implement vector-only path")