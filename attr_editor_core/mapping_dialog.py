# -*- coding: utf-8 -*-
import json
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QTableWidget, QTableWidgetItem, QTextEdit, QPushButton,
    QHeaderView, QMessageBox
)
from qgis.core import QgsProject, QgsWkbTypes

from .constants import REQUIRED_FIELDS


class MappingDialog(QDialog):
    """
    Diálogo inicial Tool 2:
    1) usuario escoge capa de líneas
    2) mapea campos existentes -> REQUIRED_FIELDS
    """
    def __init__(self, iface, initial_layer=None, initial_mapping=None, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("VIAL — Mapeo de atributos")
        self.setMinimumSize(900, 520)

        self._layers = []  # lista de QgsVectorLayer (line)
        self._combo_by_row = {}
        self._initial_mapping = initial_mapping or {}

        root = QHBoxLayout(self)

        # ----------- izquierda (selector + tabla) -----------
        left = QVBoxLayout()
        root.addLayout(left, 3)

        top = QHBoxLayout()
        left.addLayout(top)

        top.addWidget(QLabel("Capa de líneas:"))
        self.layer_combo = QComboBox()
        top.addWidget(self.layer_combo, 1)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Campo VIAL", "Campo origen en capa"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        left.addWidget(self.table, 1)

        # ----------- derecha (help) -----------
        right = QVBoxLayout()
        root.addLayout(right, 2)

        title = QLabel("Editor de atributos VIAL")
        title.setStyleSheet("font-weight: 600;")
        right.addWidget(title)

        self.help_text = QTextEdit()
        self.help_text.setReadOnly(True)
        self.help_text.setText(
            "Esta herramienta permite estandarizar los atributos de\n"
            "nomenclatura de una red vial ya existente.\n\n"
            "1) Selecciona la capa de líneas que contiene la red vial.\n"
            "2) Para cada campo estándar VIAL, elige desde qué campo actual\n"
            "   de tu capa se copiarán los valores (tipo de vía, nombre, BIS,\n"
            "   cuadrante, histórico, etc.).\n"
            "3) Al continuar, se crearán los campos faltantes y se abrirá un\n"
            "   editor interactivo para revisar, corregir y completar información."
        )
        right.addWidget(self.help_text, 1)

        # ----------- botones -----------
        btns = QHBoxLayout()
        left.addLayout(btns)
        btns.addStretch(1)

        self.btn_ok = QPushButton("OK")
        self.btn_cancel = QPushButton("Cancel")
        btns.addWidget(self.btn_ok)
        btns.addWidget(self.btn_cancel)

        self.btn_ok.clicked.connect(self._on_ok)
        self.btn_cancel.clicked.connect(self.reject)

        # cargar capas
        self._load_line_layers()
        self.layer_combo.currentIndexChanged.connect(self._rebuild_mapping_table)

        # set layer inicial
        if initial_layer is not None:
            idx = self._index_of_layer(initial_layer)
            if idx >= 0:
                self.layer_combo.setCurrentIndex(idx)

        # construir tabla inicial
        self._rebuild_mapping_table()

    def _load_line_layers(self):
        self.layer_combo.clear()
        self._layers = []

        for lyr in QgsProject.instance().mapLayers().values():
            if getattr(lyr, "type", lambda: None)() != lyr.VectorLayer:
                continue
            if lyr.geometryType() != QgsWkbTypes.LineGeometry:
                continue
            self._layers.append(lyr)
            self.layer_combo.addItem(lyr.name(), lyr.id())

    def _index_of_layer(self, layer):
        for i, lyr in enumerate(self._layers):
            if lyr and layer and lyr.id() == layer.id():
                return i
        return -1

    def _current_layer(self):
        i = self.layer_combo.currentIndex()
        if i < 0 or i >= len(self._layers):
            return None
        return self._layers[i]

    def _rebuild_mapping_table(self):
        layer = self._current_layer()
        self.table.setRowCount(0)
        self._combo_by_row.clear()

        if layer is None:
            return

        field_names = [f.name() for f in layer.fields()]
        # opción "<Sin asignar>"
        source_options = ["<Sin asignar>"] + field_names

        self.table.setRowCount(len(REQUIRED_FIELDS))

        for r, spec in enumerate(REQUIRED_FIELDS):
            item = QTableWidgetItem(spec["label"])
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 0, item)

            cb = QComboBox()
            cb.addItems(source_options)

            # aplicar mapping inicial si existe
            target_name = spec["name"]
            prev = self._initial_mapping.get(target_name)
            if prev and prev in field_names:
                cb.setCurrentText(prev)
            else:
                cb.setCurrentIndex(0)

            self.table.setCellWidget(r, 1, cb)
            self._combo_by_row[r] = cb

        self.table.resizeRowsToContents()

    def _on_ok(self):
        layer = self._current_layer()
        if layer is None:
            QMessageBox.warning(self, "VIAL", "Selecciona una capa de líneas.")
            return
        self.accept()

    def get_selected_layer_and_mapping(self):
        """
        Returns:
            (layer, mapping_dict)
            mapping_dict: { target_field_name: source_field_name or None }
        """
        layer = self._current_layer()
        mapping = {}

        for r, spec in enumerate(REQUIRED_FIELDS):
            cb = self._combo_by_row.get(r)
            if not cb:
                continue
            val = cb.currentText().strip()
            mapping[spec["name"]] = None if val == "<Sin asignar>" else val

        return layer, mapping

    @staticmethod
    def serialize_mapping(mapping: dict) -> str:
        return json.dumps(mapping, ensure_ascii=False)

    @staticmethod
    def deserialize_mapping(s: str) -> dict:
        try:
            return json.loads(s) if s else {}
        except Exception:
            return {}
