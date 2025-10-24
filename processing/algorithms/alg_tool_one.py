# file: vial_qgis_plugin/processing/algorithms/alg_tool_one.py
# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterVectorDestination,
    QgsProcessingException,
    QgsProcessingContext,
)
from qgis.utils import iface
from qgis import processing
from ...pred2graph_core.predial2roads import run_predial2roads

class VialToolOneAlg(QgsProcessingAlgorithm):
    INPUT_RASTER = "INPUT_RASTER"
    INPUT_VECTOR = "INPUT_VECTOR"     # optional
    INPUT_BOUNDARIES = "INPUT_BOUNDARIES"  # optional (polygons)
    OUTPUT = "OUTPUT"

    def tr(self, string: str) -> str:
        return QCoreApplication.translate("Vial", string)

    def name(self) -> str:
        return "tool_one"  # algorithm id -> 'vial:tool_one'

    def displayName(self) -> str:
        return self.tr("Algoritmo de Conformación Malla Vial")

    def group(self) -> str:
        return self.tr("Vial — Pred2Graph")

    def groupId(self) -> str:
        return "vial_pred2graph"

    def shortHelpString(self) -> str:
        return self.tr(
            "Entrada flexible con tres casos:\n"
            "1) Solo raster (usa Sat2Graph).\n"
            "2) Solo vector de predios (+ opcional Boundaries) → corre Predial→Roads.\n"
            "3) Raster + Vector (+ opcional Boundaries) → si hay Boundaries corre Predial→Roads."
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
                types=[QgsProcessing.TypeVectorAnyGeometry],
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
            QgsProcessingParameterVectorDestination(
                self.OUTPUT,
                self.tr("Salida (red vial)")
            )
        )

    def processAlgorithm(self, parameters, context: QgsProcessingContext, feedback):
        # Resolución de inputs
        raster = self.parameterAsRasterLayer(parameters, self.INPUT_RASTER, context)
        vector = self.parameterAsVectorLayer(parameters, self.INPUT_VECTOR, context)
        boundaries = self.parameterAsVectorLayer(parameters, self.INPUT_BOUNDARIES, context)

        # Si no se dio raster, usar el raster activo si aplica
        if raster is None:
            active = iface.activeLayer()
            if active and active.type() == 1:  # QgsMapLayerType.RasterLayer
                raster = active

        has_raster = raster is not None
        has_vector = vector is not None
        feedback.pushInfo(f"[Vial] Modo → raster={has_raster}, vector={has_vector}, boundaries={boundaries is not None}")

        # Caso 2: Solo vector (o vector + boundaries) → Predial→Roads
        if (not has_raster) and has_vector:
            outputs = run_predial2roads(vector_layer=vector, boundaries_layer=boundaries, feedback=feedback)
            result_vlayer = outputs.get("roads") or next(iter(outputs.values()))
        # Caso 3: Raster + Vector
        elif has_raster and has_vector:
            if boundaries is not None:
                outputs = run_predial2roads(vector_layer=vector, boundaries_layer=boundaries, raster_layer=raster, feedback=feedback)
                result_vlayer = outputs.get("roads") or next(iter(outputs.values()))
            else:
                # Dejar a Sat2Graph cuando no hay boundaries; import perezoso para no romper el load
                from ...sat2graph_core import utils_qgis as uq
                run_fn = getattr(uq, "run_sat2graph_raster_vector", None) or getattr(uq, "run_sat2graph_raster", None) or getattr(uq, "run_sat2graph", None)
                if run_fn is None:
                    raise QgsProcessingException("No hay función Sat2Graph disponible para raster+vector.")
                result_vlayer = run_fn(raster, vector, context, feedback) if run_fn.__code__.co_argcount >= 4 else run_fn(raster, context, feedback)
        # Caso 1: Solo raster
        elif has_raster and (not has_vector):
            from ...sat2graph_core import utils_qgis as uq
            run_fn = getattr(uq, "run_sat2graph_raster", None) or getattr(uq, "run_sat2graph", None)
            if run_fn is None:
                raise QgsProcessingException("No hay función Sat2Graph disponible para raster-only.")
            result_vlayer = run_fn(raster, context, feedback)
        else:
            raise QgsProcessingException(self.tr("Proporcione al menos un raster o un vector de predios."))

        if result_vlayer is None:
            raise QgsProcessingException(self.tr("No se obtuvo una salida válida."))

        # Normalizar: si es una ruta, cargarla como vector
        from qgis.core import QgsVectorLayer
        if isinstance(result_vlayer, str):
            tmp = QgsVectorLayer(result_vlayer, "roads", "ogr")
            if not tmp.isValid():
                raise QgsProcessingException(self.tr("La salida producida no es una capa vectorial válida."))
            result_vlayer = tmp
        else:
            # Verifica validez si es objeto capa
            if hasattr(result_vlayer, "isValid") and not result_vlayer.isValid():
                raise QgsProcessingException(self.tr("La capa de salida no es válida."))

        # Guardar en el destino del algoritmo (temporal o archivo)
        dest = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        save_res = processing.run(
            "native:savefeatures",
            {
                "INPUT": result_vlayer,
                "OUTPUT": dest
            },
            context=context,
            feedback=feedback
        )

        # Retornar el destino para que QGIS lo cargue en Layers
        return { self.OUTPUT: save_res["OUTPUT"] }