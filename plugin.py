# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMenu, QToolButton
from qgis.core import QgsApplication
from qgis import processing

from .processing.provider import VialProvider
from . import resources_rc  # ensure compiled resources are loaded


class VialPlugin(object):
    def __init__(self, iface):
        self.iface = iface
        self.canvas = self.iface.mapCanvas()

        self.provider = None
        self.menu = None
        self.toolbar = None
        self.dropdownBtn = None

        self.actions = {}

    # ---------- QGIS lifecycle ----------
    def initGui(self):
        # 1) Register Processing provider (QGIS 3.42: use QgsApplication.processingRegistry())
        self.provider = VialProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

        # 2) Build submenu under Plugins
        self.menu = QMenu('Vial', self.iface.mainWindow())
        self.menu.setIcon(QIcon(':/vial/icons/vial.svg'))

        a1 = QAction(QIcon(':/vial/icons/tool1.svg'), 'Extracción malla vial', self.iface.mainWindow())
        a2 = QAction(QIcon(':/vial/icons/tool2.svg'), 'Editor atributos de arco', self.iface.mainWindow())
        a3 = QAction(QIcon(':/vial/icons/tool3.svg'), 'Generador de nomenclatura', self.iface.mainWindow())

        # Open standard Processing parameter dialogs
        a1.triggered.connect(lambda: processing.execAlgorithmDialog('vial:tool_one'))
        a2.triggered.connect(lambda: processing.execAlgorithmDialog('vial:tool_two'))
        a3.triggered.connect(lambda: processing.execAlgorithmDialog('vial:tool_three'))

        self.menu.addAction(a1)
        self.menu.addAction(a2)
        self.menu.addAction(a3)

        # Add submenu into the Plugins menu
        self.iface.pluginMenu().addMenu(self.menu)

        # 3) Dedicated toolbar with InstantPopup dropdown
        self.toolbar = self.iface.addToolBar('Vial')
        self.toolbar.setObjectName('VialToolbar')

        self.dropdownBtn = QToolButton(self.toolbar)
        self.dropdownBtn.setText('Vial')
        self.dropdownBtn.setIcon(QIcon(':/vial/icons/vial.svg'))
        self.dropdownBtn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.dropdownBtn.setMenu(self.menu)
        self.dropdownBtn.setPopupMode(QToolButton.InstantPopup)

        self.toolbar.addWidget(self.dropdownBtn)

        # Keep references (optional bookkeeping)
        self.actions['tool1'] = a1
        self.actions['tool2'] = a2
        self.actions['tool3'] = a3

    def unload(self):
        # Remove toolbar and menu
        if self.toolbar:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar = None
        if self.menu:
            self.iface.pluginMenu().removeAction(self.menu.menuAction())
            self.menu = None

        # Unregister Processing provider
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
