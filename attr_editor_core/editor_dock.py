# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import Qt, pyqtSignal, QItemSelection, QItemSelectionModel
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTableView, QAbstractItemView,
    QGroupBox, QMessageBox
)
from qgis.PyQt.QtGui import QStandardItemModel, QStandardItem

from qgis.core import QgsVectorLayerCache
from qgis.gui import (
    QgsAttributeTableView,
    QgsAttributeTableModel,
    QgsAttributeTableFilterModel
)


class AttrEditorDock(QDockWidget):
    request_remap = pyqtSignal()   # volver al dialogo de mapeo
    dock_closed = pyqtSignal()     # para que el core guarde/oculte

    def __init__(self, iface, layer, parent=None):
        super().__init__(f"VIAL — Editor de atributos — {layer.name()}", parent)
        self.iface = iface
        self.layer = layer

        self.setObjectName("VIAL_AttrEditorDock_Tool2")

        main = QWidget(self)
        self.setWidget(main)
        layout = QVBoxLayout(main)

        # -------- Encabezado y filtros --------
        header = QHBoxLayout()
        self.lbl_layer = QLabel(f"Capa: {layer.name()}")
        header.addWidget(self.lbl_layer)

        header.addStretch(1)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filtrar por texto (...)")
        header.addWidget(self.filter_edit, 1)

        self.btn_remap = QPushButton("Volver a mapeo")
        header.addWidget(self.btn_remap)
        self.btn_remap.clicked.connect(self.request_remap.emit)

        layout.addLayout(header)

        # -------- Tabla interactiva --------
        self.cache = QgsVectorLayerCache(self.layer, 10000)

        self.model = QgsAttributeTableModel(self.cache)
        self.model.loadLayer()

        self.filter_model = QgsAttributeTableFilterModel(self.iface.mapCanvas(), self.model)
        self.filter_model.setFilterMode(QgsAttributeTableFilterModel.ShowAll)

        self.table = QgsAttributeTableView()
        self.table.setModel(self.filter_model)

        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSortingEnabled(True)
        #self.layer.selectionChanged.connect(lambda *args: self.iface.mapCanvas().refresh())


        layout.addWidget(self.table, 3)

        # --- sync guards to avoid infinite loops ---
        self._syncing_from_table = False
        self._syncing_from_layer = False

        # Tabla -> Capa
        self.table.selectionModel().selectionChanged.connect(self._on_table_selection_changed)

        # Capa -> Tabla
        self.layer.selectionChanged.connect(self._on_layer_selection_changed)


        # Filtro simple (texto) sobre displayString
        self.filter_edit.textChanged.connect(self._apply_text_filter)

        # -------- Botones acciones (placeholder) --------
        actions = QHBoxLayout()
        actions.addStretch(1)

        self.btn_contiguous = QPushButton("Identificar calles contiguas")
        self.btn_generator = QPushButton("Calcular vía generadora")
        self.btn_contiguous.setEnabled(False)
        self.btn_generator.setEnabled(False)

        actions.addWidget(self.btn_contiguous)
        actions.addWidget(self.btn_generator)
        layout.addLayout(actions)

        # -------- Sugerencias (tabla inferior NO editable) --------
        grp = QGroupBox("Sugerencias automáticas")
        grp_layout = QVBoxLayout(grp)

        self.sugg_table = QTableView()
        self.sugg_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sugg_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.sugg_table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        self.sugg_model = QStandardItemModel(0, 4)
        self.sugg_model.setHorizontalHeaderLabels(["Cadena", "Nombre sugerido", "Tramos cadena", "Estado"])
        self.sugg_table.setModel(self.sugg_model)

        grp_layout.addWidget(self.sugg_table, 1)

        sugg_btns = QHBoxLayout()
        self.btn_apply_selected = QPushButton("Aplicar sugerencias seleccionadas")
        self.btn_apply_all = QPushButton("Aceptar todas las sugerencias")
        self.btn_clear = QPushButton("Limpiar sugerencias")

        self.btn_apply_selected.clicked.connect(self._apply_selected_placeholder)
        self.btn_apply_all.clicked.connect(self._apply_all_placeholder)
        self.btn_clear.clicked.connect(self._clear_placeholder)

        sugg_btns.addWidget(self.btn_apply_selected)
        sugg_btns.addWidget(self.btn_apply_all)
        sugg_btns.addWidget(self.btn_clear)
        sugg_btns.addStretch(1)

        grp_layout.addLayout(sugg_btns)
        layout.addWidget(grp, 2)

        # Selección en sugerencias -> zoom (placeholder por ahora)
        self.sugg_table.selectionModel().selectionChanged.connect(self._on_suggestion_selected)

    def _on_table_selection_changed(self, selected, deselected):
        """
        Cuando el usuario selecciona filas en la tabla:
        - convertimos filas -> feature ids
        - seleccionamos esos ids en la capa (canvas se sincroniza automáticamente).
        """
        if self._syncing_from_layer:
            return

        self._syncing_from_table = True
        try:
            rows = self.table.selectionModel().selectedRows()
            new_fids = set()

            for idx in rows:
                try:
                    fid = int(self.filter_model.rowToId(idx))
                    new_fids.add(fid)
                except Exception:
                    pass

            # Si estamos mostrando solo seleccionados, NO colapsar la selección
            mode = self.filter_model.filterMode()
            if mode == QgsAttributeTableFilterModel.ShowSelected:
                current = set(self.layer.selectedFeatureIds())
                merged = list(current.union(new_fids))
                self.layer.selectByIds(merged)
            else:
                self.layer.selectByIds(list(new_fids))

        finally:
            self._syncing_from_table = False


    def _on_layer_selection_changed(self, selected, deselected, clearAndSelect):
        """
        Cuando el usuario selecciona en el canvas:
        - obtenemos ids seleccionados
        - los traducimos a filas visibles en el filter_model
        - seleccionamos esas filas en la tabla
        """
        if self._syncing_from_table:
            return

        self._syncing_from_layer = True
        try:
            sel_model = self.table.selectionModel()

            # Limpia selección actual (sin disparar loops fuertes)
            sel_model.blockSignals(True)
            sel_model.clearSelection()
            sel_model.blockSignals(False)

            fids = list(self.layer.selectedFeatureIds())
            if fids:
                # Modo: mostrar solo seleccionados (evita buscar en millones)
                self.filter_model.setFilterMode(QgsAttributeTableFilterModel.ShowSelected)
            else:
                self.filter_model.setFilterMode(QgsAttributeTableFilterModel.ShowAll)
                return

            item_selection = QItemSelection()

            for fid in fids:
                row = None

                # Preferimos idToRow en el filter_model si existe
                if hasattr(self.filter_model, "idToRow"):
                    row = self.filter_model.idToRow(fid)
                else:
                    # fallback: usar el modelo base y mapear
                    if hasattr(self.model, "idToRow"):
                        base_row = self.model.idToRow(fid)
                        if base_row is not None and base_row >= 0:
                            src_idx = self.model.index(base_row, 0)
                            proxy_idx = self.filter_model.mapFromSource(src_idx)
                            row = proxy_idx.row()

                if row is None or row < 0:
                    continue

                left = self.filter_model.index(row, 0)
                right = self.filter_model.index(row, self.filter_model.columnCount() - 1)
                item_selection.select(left, right)

            if not item_selection.isEmpty():
                sel_model.select(item_selection, QItemSelectionModel.Select | QItemSelectionModel.Rows)
                # Opcional: hacer scroll al primer seleccionado
                self.table.scrollTo(self.filter_model.index(item_selection.indexes()[0].row(), 0))

        finally:
            self._syncing_from_layer = False


    def _apply_text_filter(self, text: str):
        """
        Filtro simple: usa el filtro del QgsAttributeTableFilterModel.
        Nota: esto NO es el filtro avanzado del attribute table de QGIS,
        pero cumple el flujo de 'filtrar por texto' que necesitas ahora.
        """
        text = (text or "").strip()
        if not text:
            self.filter_model.setFilterString("")
            return
        self.filter_model.setFilterString(text)

    def _on_suggestion_selected(self, *args):
        """
        Placeholder: aquí luego vas a leer IDs sugeridos y hacer zoom/select en mapa.
        Por ahora no hace nada (pero deja el hook listo).
        """
        # Ejemplo futuro:
        # ids = [...]
        # self.layer.selectByIds(ids)
        pass

    def _apply_selected_placeholder(self):
        QMessageBox.information(self, "VIAL", "Lógica de sugerencias pendiente (placeholder).")

    def _apply_all_placeholder(self):
        QMessageBox.information(self, "VIAL", "Lógica de sugerencias pendiente (placeholder).")

    def _clear_placeholder(self):
        self.sugg_model.removeRows(0, self.sugg_model.rowCount())

    def closeEvent(self, e):
        """
        No destruimos estado: avisamos al core para que lo oculte/guarde.
        """
        self.dock_closed.emit()
        e.accept()
