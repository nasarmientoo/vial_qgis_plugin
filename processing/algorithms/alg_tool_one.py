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
    QgsVectorFileWriter,
    QgsMapLayerType,
)
from qgis.utils import iface


class VialToolOneAlg(QgsProcessingAlgorithm):
    INPUT_RASTER = "INPUT_RASTER"
    INPUT_VECTOR = "INPUT_VECTOR"     # optional
    OUTPUT = "OUTPUT"

    def tr(self, string: str) -> str:
        return QCoreApplication.translate("Vial", string)

    def name(self) -> str:
        return "tool_one"  # algorithm id -> 'vial:tool_one'

    def displayName(self) -> str:
        return self.tr("Algoritmo de Conformación Malla Vial")

    def group(self) -> str:
        return "Vial"

    def groupId(self) -> str:
        return "vial_group"

    def shortHelpString(self) -> str:
        return self.tr(
              "Ejecuta Sat2Graph sobre el raster seleccionado, con opción de añadir una capa vectorial.\n"
                "Crédito: Sat2Graph es un algoritmo de terceros y no fue desarrollado por este plugin.\n\n"
                "Modos de uso:\n"
                " • Solo raster → ejecuta la canalización estándar de Sat2Graph.\n"
                " • Raster + vector → ejecuta una variante que utiliza ambos insumos.\n"
                " • Solo vector → ejecuta una variante basada únicamente en la capa vectorial."
        )

    def createInstance(self):
        return VialToolOneAlg()

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.INPUT_RASTER,
                self.tr("Input raster (defaults to active layer)"),
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.INPUT_VECTOR,
                self.tr("Optional vector layer"),
                types=[QgsProcessing.TypeVectorAnyGeometry],
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterVectorDestination(
                self.OUTPUT, self.tr("Output graph (vector)")
            )
        )

    def _unpack_writer_result(self, res):
        """
        Normalize writeAsVectorFormatV3 result across QGIS versions.
        Returns: (status_code:int, out_path:str|None)
        """
        status = None
        out_path = None

        # Object-like result
        if hasattr(res, "status"):
            try:
                status = res.status()
            except Exception:
                pass
            if hasattr(res, "fileName"):
                try:
                    out_path = res.fileName()
                except Exception:
                    pass

        # Tuple/list: (status, path, [layer|id|...])
        elif isinstance(res, (tuple, list)) and len(res) >= 1:
            status = res[0]
            if len(res) >= 2:
                out_path = res[1]

        # Some builds return just the status code
        else:
            status = res

        if status is None:
            # Treat unknown as success to avoid false failures; caller still gets path fallback.
            status = QgsVectorFileWriter.NoError

        return int(status), out_path

    def processAlgorithm(self, parameters, context: QgsProcessingContext, feedback):
        # Lazy import so the plugin loads even if core deps are missing
        from ...sat2graph_core import utils_qgis as uq

        # 1) Resolve inputs
        raster = self.parameterAsRasterLayer(parameters, self.INPUT_RASTER, context)
        vector = self.parameterAsVectorLayer(parameters, self.INPUT_VECTOR, context)

        # Fallback to active raster
        if raster is None:
            active = iface.activeLayer()
            if active and active.type() == QgsMapLayerType.RasterLayer:
                raster = active

        has_raster = raster is not None
        has_vector = vector is not None
        feedback.pushInfo(f"[Vial] Mode → raster={has_raster}, vector={has_vector}")

        # 2) Route to the right code path
        if has_raster and not has_vector:
            run_fn = getattr(uq, "run_sat2graph_raster", None) or getattr(uq, "run_sat2graph", None)
            if run_fn is None:
                raise QgsProcessingException("Internal error: run_sat2graph_raster() not found.")
            result_vlayer = run_fn(raster, context, feedback)

        elif has_raster and has_vector:
            run_fn = getattr(uq, "run_sat2graph_raster_vector", None)
            if run_fn is None:
                feedback.pushInfo("[Vial] run_sat2graph_raster_vector() not implemented; falling back to raster-only.")
                run_fn = getattr(uq, "run_sat2graph_raster", None) or getattr(uq, "run_sat2graph", None)
                if run_fn is None:
                    raise QgsProcessingException("Internal error: no raster-only fallback available.")
                result_vlayer = run_fn(raster, context, feedback)
            else:
                result_vlayer = run_fn(raster, vector, context, feedback)

        elif (not has_raster) and has_vector:
            run_fn = getattr(uq, "run_sat2graph_vector_only", None)
            if run_fn is None:
                raise QgsProcessingException(
                    "Vector-only mode requested, but run_sat2graph_vector_only() is not implemented."
                )
            result_vlayer = run_fn(vector, context, feedback)

        else:
            raise QgsProcessingException("Provide at least a raster layer or a vector layer.")

        if result_vlayer is None or not result_vlayer.isValid():
            raise QgsProcessingException("Sat2Graph returned no valid output.")

        # 3) Destination path/uri
        dest = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)

        # 4) Write output
        save_opts = QgsVectorFileWriter.SaveVectorOptions()
        save_opts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
        save_opts.layerName = "Vial_Tool_1"  # stable name in e.g., GPKG

        res = QgsVectorFileWriter.writeAsVectorFormatV3(
            result_vlayer, dest, context.transformContext(), save_opts
        )
        status, out_path = self._unpack_writer_result(res)

        if status != QgsVectorFileWriter.NoError:
            try:
                msg = QgsVectorFileWriter.errorMessage(status)
            except Exception:
                msg = f"Writer error code: {status}"
            raise QgsProcessingException(f"Failed to write output: {msg}")

        # 5) Let Processing autoload the destination; do NOT add manually
        return {self.OUTPUT: out_path or dest}
