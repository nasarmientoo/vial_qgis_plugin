# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon, QColor
from qgis.PyQt.QtWidgets import QAction, QMenu, QToolButton
from qgis.core import (
    QgsApplication,
    QgsPalLayerSettings,
    QgsRuleBasedLabeling,
    QgsTextFormat,
)
from qgis.gui import QgsGui
from .processing.provider import AttrEditorProvider
from . import resources_rc
from .attr_editor_core.mapping_dialog import AttrMappingDialog
from .attr_editor_core.core import apply_field_mapping, normalize_line_direction
from .attr_editor_core.editor_dock import AttrEditorDock
import sys

# Parche para evitar errores de NumPy cuando sys.stderr es None en QGIS
if sys.stderr is None and hasattr(sys, "__stderr__"):
    sys.stderr = sys.__stderr__


class AttrEditorPlugin(object):
    def __init__(self, iface):
        self.iface = iface
        self.canvas = self.iface.mapCanvas()

        self.provider = None
        self.menu = None
        self.toolbar = None
        self.dropdownBtn = None

        self.actions = {}

        self.attr_editor_dock = None
        self._vial_layer = None
        self._vial_was_editable = False
        self._vial_undo_index = None

    # ---------- QGIS lifecycle ----------
    def initGui(self):
        # 1) Register Processing provider
        self.provider = AttrEditorProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

        # 2) Build submenu under Plugins
        self.menu = QMenu('Editor Atributos Vial', self.iface.mainWindow())
        self.menu.setIcon(QIcon(':/vial/icons/tool2.svg'))

        a2 = QAction(QIcon(':/vial/icons/tool2.svg'), 'Editor atributos de arco', self.iface.mainWindow())
        a2.triggered.connect(self.run_attr_editor_flow)

        self.menu.addAction(a2)
        self.iface.pluginMenu().addMenu(self.menu)

        # 3) Dedicated toolbar
        self.toolbar = self.iface.addToolBar('Editor Atributos Vial')
        self.toolbar.setObjectName('AttrEditorToolbar')

        self.dropdownBtn = QToolButton(self.toolbar)
        self.dropdownBtn.setText('Attr Editor')
        self.dropdownBtn.setIcon(QIcon(':/vial/icons/tool2.svg'))
        self.dropdownBtn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.dropdownBtn.setMenu(self.menu)
        self.dropdownBtn.setPopupMode(QToolButton.InstantPopup)

        self.toolbar.addWidget(self.dropdownBtn)
        self.actions['tool2'] = a2

    def run_attr_editor_flow(self):
        """
        1) Muestra diálogo de mapeo de atributos.
        2) Crea campos estándar y transfiere valores.
        3) Abre el dock de edición de atributos.
        """
        dlg = AttrMappingDialog(self.iface, self.iface.mainWindow())
        if dlg.exec_() != dlg.Accepted:
            return

        layer = dlg.selectedLayer()
        mapping = dlg.fieldMapping()

        if not layer:
            return

        self._vial_layer = layer
        self._vial_was_editable = layer.isEditable()

        if not layer.isEditable():
            layer.startEditing()

        try:
            self._vial_undo_index = layer.undoStack().index()
        except Exception:
            self._vial_undo_index = None

        layer.beginEditCommand("VIAL: mapeo inicial (campos + transferencia)")
        try:
            apply_field_mapping(layer, mapping)
        finally:
            layer.endEditCommand()

        normalize_line_direction(layer)
        self._enable_vial_labels(layer)

        if self.attr_editor_dock is not None:
            self.iface.removeDockWidget(self.attr_editor_dock)
            self.attr_editor_dock.deleteLater()
            self.attr_editor_dock = None

        self.attr_editor_dock = AttrEditorDock(
            self.iface,
            layer,
            self.iface.mainWindow(),
            plugin=self,
        )

        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.attr_editor_dock)
        self.attr_editor_dock.show()

    def _enable_vial_labels(self, layer):
        """
        Activa etiquetas en la capa con dos reglas:
        - Arriba de la linea: atributos de via principal.
        - Abajo de la linea: atributos de via generadora.
        """
        if layer is None:
            return

        expr_main = (
            "trim("
            "coalesce(\"tipo_via\", '') || ' ' || "
            "coalesce(\"numero_via\", '') || "
            "coalesce(' ' || \"letra_principal\", '') || "
            "coalesce(' ' || \"prefijo_principal\", '') || "
            "coalesce(' ' || \"letra_prefijo_principal\", '') || "
            "coalesce(' ' || \"cuadrante_principal\", '')"
            ")"
        )

        expr_gen = (
            "trim("
            "coalesce(\"num_generadora\", '') || "
            "coalesce(' ' || \"letra_generadora\", '') || "
            "coalesce(' ' || \"sufijo_generadora\", '') || "
            "coalesce(' ' || \"letra_sufijo_generadora\", '') || "
            "coalesce(' ' || \"cuadrante_generadora\", '')"
            ")"
        )

        top_settings = QgsPalLayerSettings()
        top_settings.fieldName = expr_main
        top_settings.isExpression = True
        top_settings.placement = QgsPalLayerSettings.Line
        top_settings.placementFlags = QgsPalLayerSettings.AboveLine
        top_settings.dist = 1
        top_format = QgsTextFormat()
        top_format.setColor(QColor(0, 102, 204))
        top_settings.setFormat(top_format)

        bottom_settings = QgsPalLayerSettings()
        bottom_settings.fieldName = expr_gen
        bottom_settings.isExpression = True
        bottom_settings.placement = QgsPalLayerSettings.Line
        bottom_settings.placementFlags = QgsPalLayerSettings.BelowLine
        bottom_settings.dist = 1
        bottom_format = QgsTextFormat()
        bottom_format.setColor(QColor(255, 153, 0))
        bottom_settings.setFormat(bottom_format)

        root = QgsRuleBasedLabeling.Rule(QgsPalLayerSettings())
        root.appendChild(QgsRuleBasedLabeling.Rule(top_settings))
        root.appendChild(QgsRuleBasedLabeling.Rule(bottom_settings))

        layer.setLabeling(QgsRuleBasedLabeling(root))
        layer.setLabelsEnabled(True)
        layer.triggerRepaint()

    def unload(self):
        if self.attr_editor_dock:
            self.iface.removeDockWidget(self.attr_editor_dock)
            self.attr_editor_dock = None
        if self.toolbar:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar = None
        if self.menu:
            self.iface.pluginMenu().removeAction(self.menu.menuAction())
            self.menu = None
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
