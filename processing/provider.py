# -*- coding: utf-8 -*-
from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProcessingProvider, QgsMessageLog, Qgis

from .algorithms.alg_tool_two import VialToolTwoAlg


class AttrEditorProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        try:
            self.addAlgorithm(VialToolTwoAlg())
        except Exception as e:
            QgsMessageLog.logMessage(
                f'Failed to add VialToolTwoAlg: {e}', 'AttrEditor', Qgis.Critical
            )

    def id(self):
        return 'attreditor'

    def name(self):
        return 'Editor Atributos Vial'

    def longName(self):
        return 'Editor de Atributos de Arco Vial'

    def icon(self):
        return QIcon(':/vial/icons/tool2.svg')
