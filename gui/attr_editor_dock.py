# -*- coding: utf-8 -*-
# vial_qgis_plugin/gui/attr_editor_dock.py

from qgis.PyQt import QtWidgets, QtCore
from qgis.gui import QgsMapLayerComboBox, QgsAttributeTableView, QgsAttributeTableModel
from qgis.core import QgsMapLayerProxyModel

class VialAttrEditorDock(QtWidgets.QDockWidget):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("Vial — Editor de atributos")
        self.setObjectName("VialAttrEditorDock")
        self.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)

        # Top: capa + filtros
        self.layerCombo = QgsMapLayerComboBox()
        self.layerCombo.setFilters(QgsMapLayerProxyModel.LineLayer)
        self.filterEdit = QtWidgets.QLineEdit()
        self.filterEdit.setPlaceholderText("Filtrar por nombre de vía…")
        self.onlySelectedChk = QtWidgets.QCheckBox("Solo seleccionados")

        topRow = QtWidgets.QHBoxLayout()
        topRow.addWidget(QtWidgets.QLabel("Capa:"))
        topRow.addWidget(self.layerCombo, 1)
        topRow.addWidget(self.onlySelectedChk)
        topRow.addWidget(self.filterEdit)

        topBox = QtWidgets.QGroupBox("Selección y filtros")
        topBox.setLayout(topRow)

        # Tabla
        self.tableView = QgsAttributeTableView()
        self.tableView.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tableView.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
            | QtWidgets.QAbstractItemView.AnyKeyPressed
        )
        self.attrModel = None

        # Sugerencias (placeholder)
        self.sugLabel = QtWidgets.QLabel("Sugerencias: (aquí aparecerán propuestas basadas en contigüidad)")
        self.applyAllBtn = QtWidgets.QPushButton("Aplicar a contiguos")
        self.applyAllBtn.setEnabled(False)

        sugLayout = QtWidgets.QVBoxLayout()
        sugLayout.addWidget(self.sugLabel)
        sugLayout.addWidget(self.applyAllBtn, 0, QtCore.Qt.AlignLeft)
        sugBox = QtWidgets.QGroupBox("Sugerencias automáticas")
        sugBox.setLayout(sugLayout)

        # Acciones
        self.startEditBtn = QtWidgets.QPushButton("Iniciar edición")
        self.commitBtn = QtWidgets.QPushButton("Guardar")
        self.rollbackBtn = QtWidgets.QPushButton("Descartar")

        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(self.startEditBtn)
        bottom.addWidget(self.commitBtn)
        bottom.addWidget(self.rollbackBtn)

        # Layout principal
        central = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(central)
        vbox.addWidget(topBox)
        vbox.addWidget(self.tableView, 1)
        vbox.addWidget(sugBox)
        vbox.addLayout(bottom)
        self.setWidget(central)

        # Conexiones mínimas
        self.layerCombo.layerChanged.connect(self._on_layer_changed)
        self.startEditBtn.clicked.connect(self._on_start_edit)
        self.commitBtn.clicked.connect(self._on_commit)
        self.rollbackBtn.clicked.connect(self._on_rollback)

    def _on_layer_changed(self, layer):
        if not layer:
            self.attrModel = None
            self.tableView.setModel(None)
            return
        model = QgsAttributeTableModel(layer)
        model.loadLayer()
        self.attrModel = model
        self.tableView.setModel(model)

    def _on_start_edit(self):
        lyr = self.layerCombo.currentLayer()
        if lyr and not lyr.isEditable():
            lyr.startEditing()

    def _on_commit(self):
        lyr = self.layerCombo.currentLayer()
        if lyr and lyr.isEditable():
            lyr.commitChanges()

    def _on_rollback(self):
        lyr = self.layerCombo.currentLayer()
        if lyr and lyr.isEditable():
            lyr.rollBack()
