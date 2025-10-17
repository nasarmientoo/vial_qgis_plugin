# -*- coding: utf-8 -*-
from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProcessingProvider, QgsMessageLog, Qgis

from .algorithms.alg_tool_one import VialToolOneAlg
from .algorithms.alg_tool_two import VialToolTwoAlg
from .algorithms.alg_tool_three import VialToolThreeAlg

class VialProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        for alg in (VialToolOneAlg(), VialToolTwoAlg(), VialToolThreeAlg()):
            try:
                self.addAlgorithm(alg)
            except Exception as e:
                QgsMessageLog.logMessage(
                    f'Failed to add {type(alg).__name__}: {e}', 'Vial', Qgis.Critical
                )

    def id(self):
        return 'vial'

    def name(self):
        return 'Vial'

    def longName(self):
        return 'Vial Tools'

    def icon(self):
        # requires resources_rc.py to be loaded (imported in plugin.py)
        return QIcon(':/vial/icons/vial.svg')
