# =============================
# Archivo: mapping_dialog.py (py39-safe)
# -*- coding: utf-8 -*-
# =============================
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QTableWidget, QTableWidgetItem,
    QDialogButtonBox, QTextBrowser
)
from qgis.core import QgsProject, QgsMapLayer, QgsWkbTypes

from .core import REQUIRED_FIELDS  # mismo módulo donde definimos los campos estándar


class AttrMappingDialog(QDialog):
    """
    Diálogo para:
    1) Seleccionar capa de líneas.
    2) Definir mapeo campo VIAL -> campo origen.
    """
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._layer = None

        self.setWindowTitle("VIAL — Editor de Nomenclatura (Mapeo de Atributos)")
        self.resize(800, 500)

        self.layer_combo = QComboBox()
        self.table = QTableWidget()
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)

        self._init_ui()
        self._populate_layers()

        self.layer_combo.currentIndexChanged.connect(self._on_layer_changed)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

    # ---------- API pública ----------

    def selectedLayer(self):
        return self._layer

    def fieldMapping(self):
        """
        Retorna el mapeo de los campos de la capa seleccionada a los campos estándar de "VIAL".
        """
        mapping = {}
        if not self._layer:
            return mapping

        for row, spec in enumerate(REQUIRED_FIELDS):
            target_name = spec["name"]
            combo = self.table.cellWidget(row, 1)
            if combo is None:
                mapping[target_name] = None
            else:
                mapping[target_name] = combo.currentData()
        return mapping

    # ---------- UI interna ----------

    def _init_ui(self):
        main_layout = QVBoxLayout(self)

        # Fila de selección de capa
        top = QHBoxLayout()
        top.addWidget(QLabel("Capa de líneas:"))
        top.addWidget(self.layer_combo)
        main_layout.addLayout(top)

        # Configuración base de la tabla
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Campo VIAL", "Campo origen en capa"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)

        # Zona central: tabla (izquierda) + descripción (derecha)
        center = QHBoxLayout()
        center.addWidget(self.table, 3)

        self.desc_browser = QTextBrowser()
        self.desc_browser.setReadOnly(True)
        self.desc_browser.setMinimumWidth(200)
        self.desc_browser.setMaximumWidth(300)
        self.desc_browser.setHtml(
            "<b>✎ Editor de Nomenclatura Vial</b><br><br>"
            "<b>Propósito:</b> Estandarizar y validar los atributos de nomenclatura "
            "de una red vial ya existente según normativa.<br><br>"
            "<b>Flujo de trabajo:</b><br>"
            "<b style='color: #0066cc'>1) Selecciona la capa</b><br>"
            "Elige la capa de líneas (vías) que deseas estandarizar.<br><br>"
            "<b style='color: #0066cc'>2) Mapea los campos</b><br>"
            "Para cada campo estándar VIAL (tipo_via, nombre_via, numero_via, letra, "
            "BIS, cuadrante, etc.), selecciona el campo correspondiente en tu capa actual. "
            "Si tu capa ya tiene los nombres estándar, se autodetectarán. "
            "Puedes dejar campos sin mapear si no existen en tu capa.<br><br>"
            "<b style='color: #0066cc'>3) Creación de campos faltantes</b><br>"
            "Al continuar, el sistema creará automáticamente los campos estándar VIAL "
            "que no existan en tu capa, y transferirá los valores desde los campos "
            "mapeados.<br><br>"
            "<b style='color: #0066cc'>4) Editor interactivo</b><br>"
            "Se abrirá un editor donde podrás:<br>"
            "• Revisar y corregir cada atributo<br>"
            "• Las reglas de validación se aplican automáticamente<br>"
            "• Ver el histórico de cambios (campo 'historico_nom')<br>"
            "• Identificar calles contiguas alineadas<br>"
            "• Guardar o descartar cambios (reversible)<br><br>"
            "<b>⚠️ Nota:</b> Todo texto se normaliza a MAYÚSCULAS. "
            "Se prohíben 'NO APLICA', puntuación y caracteres especiales "
            "(excepto '/' en actos administrativos)."
        )
        center.addWidget(self.desc_browser, 2)

        main_layout.addLayout(center)
        main_layout.addWidget(self.buttons)

    def _populate_layers(self):
        self.layer_combo.clear()
        proj = QgsProject.instance()
        for layer in proj.mapLayers().values():
            if layer.type() != QgsMapLayer.VectorLayer:
                continue
            if layer.geometryType() != QgsWkbTypes.LineGeometry:
                continue
            self.layer_combo.addItem(layer.name(), layer.id())

        if self.layer_combo.count() > 0:
            self._on_layer_changed(0)
        else:
            # Si no hay capas, dejamos la tabla vacía
            self._layer = None
            self.table.clearContents()
            self.table.setRowCount(0)

    def _on_layer_changed(self, index):
        if index < 0:
            self._layer = None
            self.table.clearContents()
            self.table.setRowCount(0)
            return

        proj = QgsProject.instance()
        layer_id = self.layer_combo.itemData(index)
        self._layer = proj.mapLayer(layer_id)

        if not self._layer:
            self.table.clearContents()
            self.table.setRowCount(0)
            return

        self._build_mapping_table()

    def _build_mapping_table(self):
        """
        Rellena la tabla con una fila por cada campo estándar VIAL
        y un combo con los campos de la capa.
        """
        self.table.clearContents()
        self.table.setRowCount(len(REQUIRED_FIELDS))

        fields = self._layer.fields()
        field_names = [f.name() for f in fields]

        for row, spec in enumerate(REQUIRED_FIELDS):
            # Columna 0: nombre amigable del campo VIAL
            item = QTableWidgetItem(spec["alias"])
            # Hacer que la celda no sea editable
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, item)

            # Columna 1: combo con campos origen
            combo = QComboBox()
            combo.addItem("<Sin asignar>", None)
            for name in field_names:
                combo.addItem(name, name)
            self.table.setCellWidget(row, 1, combo)
        self.table.resizeColumnsToContents()
