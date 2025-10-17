# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import QgsProcessing, QgsProcessingAlgorithm

class VialToolThreeAlg(QgsProcessingAlgorithm):
    def tr(self, s): return QCoreApplication.translate('Vial', s)

    def name(self): return 'tool_three'
    def displayName(self): return self.tr('Tool 3 — Placeholder')
    def group(self): return 'Vial'
    def groupId(self): return 'vial_group'
    def createInstance(self): return VialToolThreeAlg()
    def initAlgorithm(self, config=None): pass
    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo('Tool 3 clicked (no operation).')
        return {}
