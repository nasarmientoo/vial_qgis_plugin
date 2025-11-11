# file: vial_qgis_plugin/processing/algorithms/alg_tool_one.py
# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterString,
    QgsProcessingParameterDefinition,
    QgsWkbTypes, QgsProject,
    QgsProcessingParameterVectorDestination,
    QgsProcessingException,
    QgsProcessingContext,
    QgsVectorLayer
)
from qgis.utils import iface
from qgis import processing
from qgis.PyQt.QtCore import QVariant
from ...pred2graph_core.predial2roads import run_predial2roads
from ...utils_common.unify_roads import fuse_any_layers_by_priority

class VialToolOneAlg(QgsProcessingAlgorithm):
    INPUT_RASTER = "INPUT_RASTER"
    INPUT_VECTOR = "INPUT_VECTOR"     
    INPUT_BOUNDARIES = "INPUT_BOUNDARIES"  
    ROAD_LAYERS = "ROAD_LAYERS"        
    ORDERED_IDS = "ORDERED_IDS"        
    OUTPUT = "OUTPUT"

    def tr(self, string: str) -> str:
        return QCoreApplication.translate("Vial", string)

    def name(self) -> str:
        return "tool_one"  # algorithm id -> 'vial:tool_one'

    def displayName(self) -> str:
        return self.tr("Algoritmo de Conformación Malla Vial")

    def group(self) -> str:
        return self.tr("Vial")

    def groupId(self) -> str:
        return "vial"

    def shortHelpString(self) -> str:
        return self.tr(
            "Herramienta para la conformación automática y asistida de la red vial a partir de diferentes fuentes.\n\n"
            "• Caso 1 — Desde raster (Sat2Graph): Extrae la malla vial a partir de una imagen satelital o raster. "
            "Requiere entorno Docker con dependencias de visión por computador. "
            "Basado en el trabajo *Sat2Graph: Road Graph Extraction through Graph-Tensor Encoding* (He et al.), disponible en: https://github.com/songtaohe/Sat2Graph\n\n"
            "• Caso 2 — Desde base predial: Genera la red vial a partir de predios urbanos y, opcionalmente, límites municipales, "
            "usando relaciones geométricas y topológicas entre polígonos.\n\n"
            "• Caso 3 — Integración de mallas viales: Fusiona varias capas de líneas priorizadas (por ejemplo, OSM e insumos institucionales) "
            "mediante un proceso iterativo de buffer y recorte, generando una red unificada y limpia."
        )

    def createInstance(self):
        return VialToolOneAlg()

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.INPUT_RASTER,
                self.tr("Input raster"),
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_VECTOR,
                self.tr("Vector de predios"),
                types=[QgsProcessing.TypeVectorPolygon],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_BOUNDARIES,
                self.tr("Límites municipales"),
                types=[QgsProcessing.TypeVectorPolygon],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterMultipleLayers(
                self.ROAD_LAYERS,
                self.tr("Mallas viales (línea) — seleccione una o más"),
                layerType=QgsProcessing.TypeVectorLine,
                optional=True
            )
        )
        # Parámetro oculto para guardar el orden decidido en el panel drag&drop
        p = QgsProcessingParameterString(self.ORDERED_IDS, self.tr("Orden de prioridad (interno)"), defaultValue="", optional=True)
        p.setFlags(p.flags() |  QgsProcessingParameterDefinition.FlagHidden)
        self.addParameter(p)

        self.addParameter(
            QgsProcessingParameterVectorDestination(
                self.OUTPUT,
                self.tr("Salida (red vial)")
            )
        )

    def _cancel(self, feedback):
        if feedback.isCanceled():
            raise QgsProcessingException("Operación cancelada por el usuario.")


    def processAlgorithm(self, parameters, context: QgsProcessingContext, feedback):
        self._cancel(feedback)
        # 1) Resolver inputs existentes
        raster = self.parameterAsRasterLayer(parameters, self.INPUT_RASTER, context)
        vector = self.parameterAsVectorLayer(parameters, self.INPUT_VECTOR, context)
        boundaries = self.parameterAsVectorLayer(parameters, self.INPUT_BOUNDARIES, context)

        # 2) Nuevo: leer mallas viales y el orden (si lo hay)
        road_layers = self.parameterAsLayerList(parameters, self.ROAD_LAYERS, context) or []
        ordered_ids_csv = self.parameterAsString(parameters, self.ORDERED_IDS, context) or ""

        # 3) Si hay mallas → CASO 3 (tiene prioridad)
        self._cancel(feedback)
        if road_layers:
            # reordenar según ORDERED_IDS si está definido
            if ordered_ids_csv.strip():
                id2lyr = {lyr.id(): lyr for lyr in road_layers}
                ordered = [id2lyr[lid] for lid in ordered_ids_csv.split(",") if lid in id2lyr]
                # insertar cualquiera que no haya quedado (por si cambió selección después de ordenar)
                for lyr in road_layers:
                    if lyr not in ordered:
                        ordered.append(lyr)
                layers_in_order = ordered
            else:
                layers_in_order = road_layers

            # parámetros "quemados" (NO se muestran en UI)
            _BUFFER_M = 10.0
            _POST_CLEAN = False
            _SHOW_DEBUG = False

            result_vlayer = fuse_any_layers_by_priority(
                layers_in_order,
                buffer_m=_BUFFER_M,
                post_clean=_POST_CLEAN,
                show_debug=_SHOW_DEBUG,
                final_name="final_fusion_malla_vial"
            )

            if result_vlayer is None or (hasattr(result_vlayer, "isValid") and not result_vlayer.isValid()):
                raise QgsProcessingException(self.tr("No se obtuvo una salida válida en la fusión de mallas."))

            dest = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
            self._cancel(feedback)
            save_res = processing.run(
                "native:savefeatures",
                {"INPUT": result_vlayer, "OUTPUT": dest},
                context=context, feedback=feedback
            )
            self._cancel(feedback)
            return { self.OUTPUT: save_res["OUTPUT"] }

        # 4) Si NO hay mallas → comportamientos existentes (caso 1 y 2)
        # (tu código original, sin cambios de nombres)
        # --------------------------------------------------------------
        # Si no se dio raster, usar el raster activo si aplica
        if raster is None:
            active = iface.activeLayer()
            if active and active.type() == 1:
                raster = active

        has_raster = raster is not None
        has_vector = vector is not None
        feedback.pushInfo(f"[Vial] Modo → raster={has_raster}, vector={has_vector}, boundaries={boundaries is not None}")

        if (not has_raster) and has_vector:
            from ...pred2graph_core.predial2roads import run_predial2roads
            outputs = run_predial2roads(vector_layer=vector, boundaries_layer=boundaries, feedback=feedback)
            self._cancel(feedback)
            result_vlayer = outputs.get("roads") or next(iter(outputs.values()))
        elif has_raster and (not has_vector):
            from ...sat2graph_core import utils_qgis as uq
            run_fn = getattr(uq, "run_sat2graph_raster", None) or getattr(uq, "run_sat2graph", None)
            if run_fn is None:
                raise QgsProcessingException("No hay función Sat2Graph disponible para raster-only.")
            self._cancel(feedback)
            result_vlayer = run_fn(raster, context, feedback)
        else:
            raise QgsProcessingException(self.tr("Proporcione al menos un raster o un vector de predios."))

        if result_vlayer is None:
            raise QgsProcessingException(self.tr("No se obtuvo una salida válida."))

        if isinstance(result_vlayer, str):
            tmp = QgsVectorLayer(result_vlayer, "roads", "ogr")
            if not tmp.isValid():
                raise QgsProcessingException(self.tr("La salida producida no es una capa vectorial válida."))
            result_vlayer = tmp
        else:
            if hasattr(result_vlayer, "isValid") and not result_vlayer.isValid():
                raise QgsProcessingException(self.tr("La capa de salida no es válida."))

        dest = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        save_res = processing.run(
            "native:savefeatures",
            {"INPUT": result_vlayer, "OUTPUT": dest},
            context=context, feedback=feedback
        )
        return { self.OUTPUT: save_res["OUTPUT"] }
