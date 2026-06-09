# =============================
# Archivo: editor_dock.py (py39-safe)
# -*- coding: utf-8 -*-
# =============================
# arriba del archivo
import json
import math

from qgis.PyQt.QtCore import Qt,QSize, QItemSelection, QItemSelectionModel, QModelIndex, QDateTime, QVariant
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTableView, QAbstractItemView,
    QMessageBox, QComboBox, QToolButton, QDialog, QGroupBox,  QStyle, QAbstractItemDelegate, QSizePolicy
)
from qgis.PyQt.QtGui import QStandardItemModel, QStandardItem, QColor
from qgis.gui import QgsAttributeTableModel, QgsAttributeTableFilterModel, QgsAttributeTableDelegate 
from qgis.core import QgsVectorLayerCache, Qgis, QgsFeature, QgsApplication, QgsSpatialIndex, QgsMessageLog, QgsField, QgsVectorLayer, QgsWkbTypes, QgsGeometry
from qgis import processing

from .core import REQUIRED_FIELDS

from .vial_reglas import (
    get_vial_snapshot_fields_cfg,
    get_watched_attr_idxs,
    FIELD_VALIDATORS,
    TIPO_VIA_CHOICES,
    CUADRANTE_CHOICES,
)

from ..utils_common.road_chain_merger import compute_chains_layer

class VialAttributeDelegate(QgsAttributeTableDelegate):
    """
    Delegate personalizado para:
    - Mostrar un QComboBox en el campo tipo_via con el catálogo oficial.
    - Guardar el código de 2 caracteres (CL, KR, TV, etc.) directamente en la capa
      usando changeAttributeValue (NO model.setData), porque en algunos setups
      QgsAttributeTableModel.setData devuelve False.
    """

    def __init__(self, parent, layer, attr_model, proxy_model, iface):
        super().__init__(parent)
        self.layer = layer                    # QgsVectorLayer
        self.attr_model = attr_model          # QgsAttributeTableModel (fuente)
        self.proxy_model = proxy_model        # QgsAttributeTableFilterModel (proxy)
        self.iface = iface                    # QgisInterface (para mostrar warnings)

    # ----------------- helpers -----------------

    def _source_index(self, index: QModelIndex) -> QModelIndex:
        """Convierte índice del proxy al índice del modelo fuente."""
        if not index.isValid():
            return QModelIndex()
        try:
            return self.proxy_model.mapToSource(index)
        except Exception:
            return index

    def _field_name_for_index(self, index: QModelIndex):
        """A partir de un índice del proxy, obtiene el nombre real del campo de la capa."""
        if not index.isValid() or self.layer is None:
            return None

        src_index = self._source_index(index)
        if not src_index.isValid():
            return None

        src_col = src_index.column()
        if src_col == 0:
            return None

        field_idx = self.attr_model.fieldIdx(src_col)
        if field_idx < 0:
            return None

        return self.layer.fields()[field_idx].name()

    def _fid_for_index(self, index: QModelIndex):
        """Devuelve el fid real asociado a la fila del índice (proxy -> source)."""
        if not index.isValid():
            return None

        src_index = self._source_index(index)
        if not src_index.isValid():
            return None

        src_row = src_index.row()
        fid = self.attr_model.rowToId(src_row)
        if fid is None or fid < 0:
            return None

        return fid

    def _apply_value_to_selected(self, field_idx, value, primary_fid):
        """Aplica el mismo valor SOLO si el usuario realmente seleccionó múltiples filas/celdas en la TABLA."""
        if self.layer is None:
            return False

        # ✅ tomar selección desde la tabla (no desde layer.selectedFeatureIds())
        view = self.parent()  # QTableView
        sel_model = view.selectionModel() if view else None
        if not sel_model or not sel_model.hasSelection():
            return False

        # Obtener FIDs únicos a partir de las filas/celdas seleccionadas en la tabla
        selected_qgis_fids = set()
        for idx in sel_model.selectedIndexes():
            if not idx.isValid():
                continue
            src_index = self._source_index(idx)
            if not src_index.isValid():
                continue
            fid = self.attr_model.rowToId(src_index.row())
            if fid is not None and fid >= 0:
                selected_qgis_fids.add(int(fid))

        if len(selected_qgis_fids) <= 1 or primary_fid not in selected_qgis_fids:
            return False

        # Asegurar edición
        if not self.layer.isEditable():
            if not self.layer.startEditing():
                QgsMessageLog.logMessage(
                    "[VIAL] No se pudo iniciar edición para edición múltiple.",
                    "VIAL",
                    Qgis.Critical,
                )
                return False

        self.layer.beginEditCommand("VIAL: edicion multiple")
        changed = False
        try:
            for fid in selected_qgis_fids:
                if fid == primary_fid:
                    continue
                self.layer.changeAttributeValue(fid, field_idx, value)
                changed = True
        finally:
            self.layer.endEditCommand()

        if changed:
            self.layer.triggerRepaint()

        return changed


    # ----------------- editor -----------------

    def createEditor(self, parent, option, index):
        field_name = self._field_name_for_index(index)

        if field_name == "tipo_via":
            combo = QComboBox(parent)
            for code, label in TIPO_VIA_CHOICES:
                # Para la opción sin asignar, mostrar solo el label
                if code is None:
                    combo.addItem(label, None)
                else:
                    combo.addItem(f"{code} – {label}", code)

            # 🔑 Forzar commit al cambiar selección
            combo.activated.connect(lambda *_: self.commitData.emit(combo))
            combo.activated.connect(
                lambda *_: self.closeEditor.emit(combo, QAbstractItemDelegate.NoHint)
            )
            return combo
        
        if field_name == "cuadrante_principal":
            combo = QComboBox(parent)
            for code, label in CUADRANTE_CHOICES:
                # Para la opción sin asignar, mostrar solo el label
                if code is None:
                    combo.addItem(label, None)
                else:
                    combo.addItem(f"{code} – {label}", code)

            # 🔑 Forzar commit al cambiar selección
            combo.activated.connect(lambda *_: self.commitData.emit(combo))
            combo.activated.connect(
                lambda *_: self.closeEditor.emit(combo, QAbstractItemDelegate.NoHint)
            )
            return combo
        
        if field_name == "cuadrante_generadora":
            combo = QComboBox(parent)
            for code, label in CUADRANTE_CHOICES:
                # Para la opción sin asignar, mostrar solo el label
                if code is None:
                    combo.addItem(label, None)
                else:
                    combo.addItem(f"{code} – {label}", code)

            # 🔑 Forzar commit al cambiar selección
            combo.activated.connect(lambda *_: self.commitData.emit(combo))
            combo.activated.connect(
                lambda *_: self.closeEditor.emit(combo, QAbstractItemDelegate.NoHint)
            )
            return combo

        return super().createEditor(parent, option, index)

    def setEditorData(self, editor, index):
        field_name = self._field_name_for_index(index)

        if field_name == "tipo_via" and isinstance(editor, QComboBox):
            fid = self._fid_for_index(index)
            if fid is None or self.layer is None:
                return

            # Leer el valor actual directamente desde la capa
            idx_tipo = self.layer.fields().indexOf("tipo_via")
            if idx_tipo < 0:
                return

            feat = self.layer.getFeature(fid)
            if not feat.isValid():
                return

            current_val = feat[idx_tipo]
            # Si es None o vacío, seleccionar la opción "<sin asignar>"
            if current_val is None or str(current_val).strip() == "":
                current_code = None
            else:
                current_code = str(current_val).strip().upper()

            # Posicionar el combo por data (código)
            for i in range(editor.count()):
                if editor.itemData(i) == current_code:
                    editor.setCurrentIndex(i)
                    break
            return
        
        if field_name == "cuadrante_principal" and isinstance(editor, QComboBox):
            fid = self._fid_for_index(index)
            if fid is None or self.layer is None:
                return

            # Leer el valor actual directamente desde la capa
            idx_cuadrante = self.layer.fields().indexOf("cuadrante_principal")
            if idx_cuadrante < 0:
                return

            feat = self.layer.getFeature(fid)
            if not feat.isValid():
                return

            current_val = feat[idx_cuadrante]
            # Si es None o vacío, seleccionar la opción "<sin asignar>"
            if current_val is None or str(current_val).strip() == "":
                current_code = None
            else:
                current_code = str(current_val).strip().upper()

            # Posicionar el combo por data (código)
            for i in range(editor.count()):
                if editor.itemData(i) == current_code:
                    editor.setCurrentIndex(i)
                    break
            return
        
        if field_name == "cuadrante_generadora" and isinstance(editor, QComboBox):
            fid = self._fid_for_index(index)
            if fid is None or self.layer is None:
                return

            # Leer el valor actual directamente desde la capa
            idx_cuadrante = self.layer.fields().indexOf("cuadrante_generadora")
            if idx_cuadrante < 0:
                return

            feat = self.layer.getFeature(fid)
            if not feat.isValid():
                return

            current_val = feat[idx_cuadrante]
            # Si es None o vacío, seleccionar la opción "<sin asignar>"
            if current_val is None or str(current_val).strip() == "":
                current_code = None
            else:
                current_code = str(current_val).strip().upper()

            # Posicionar el combo por data (código)
            for i in range(editor.count()):
                if editor.itemData(i) == current_code:
                    editor.setCurrentIndex(i)
                    break
            return

        super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        field_name = self._field_name_for_index(index)

        # Para campos con validadores, validar ANTES de guardar
        # y mostrar warning al usuario si hay error
        if field_name in FIELD_VALIDATORS and not isinstance(editor, QComboBox):
            # Obtener el valor actual del editor
            if isinstance(editor, QLineEdit):
                raw_value = editor.text()
            elif isinstance(editor, QTextEdit):
                raw_value = editor.toPlainText()
            else:
                # Para otros tipos de editor, dejar que el flujo normal continúe
                raw_value = None

            if raw_value is not None:
                validator = FIELD_VALIDATORS[field_name]
                normalized, error_msg = validator(raw_value)

                if error_msg:
                    # Mostrar warning al usuario
                    self.iface.messageBar().pushMessage(
                        "VIAL",
                        error_msg,
                        Qgis.Warning,
                        4,
                    )
                    # No guardar el valor inválido - cancelar la edición
                    return

                # Si hay normalización, actualizar el editor con el valor normalizado
                if normalized != raw_value:
                    if isinstance(editor, QLineEdit):
                        editor.setText(normalized if normalized else "")
                    elif isinstance(editor, QTextEdit):
                        editor.setPlainText(normalized if normalized else "")

        if field_name == "tipo_via" and isinstance(editor, QComboBox):
            if self.layer is None:
                return

            fid = self._fid_for_index(index)
            if fid is None:
                return

            new_code = editor.currentData()
            # Permitir None como valor válido (sin asignar)

            idx_tipo = self.layer.fields().indexOf("tipo_via")
            if idx_tipo < 0:
                return

            # Asegurar modo edición
            if not self.layer.isEditable():
                if not self.layer.startEditing():
                    QgsMessageLog.logMessage(
                        "[VIAL] No se pudo iniciar edición para guardar tipo_via.",
                        "VIAL",
                        Qgis.Critical
                    )
                    return

            ok = self.layer.changeAttributeValue(fid, idx_tipo, new_code)

            # Si hay selección múltiple, aplicar el mismo valor a todos los seleccionados
            self._apply_value_to_selected(idx_tipo, new_code, fid)

            # Forzar refresco de la celda en la tabla (visual)
            try:
                src_index = self._source_index(index)
                self.attr_model.dataChanged.emit(
                    src_index, src_index, [Qt.DisplayRole, Qt.EditRole]
                )
            except Exception:
                pass

            return
        
        if field_name == "cuadrante_principal" and isinstance(editor, QComboBox):
            if self.layer is None:
                return

            fid = self._fid_for_index(index)
            if fid is None:
                return

            new_code = editor.currentData()
            # Permitir None como valor válido (sin asignar)

            idx_cuadrante = self.layer.fields().indexOf("cuadrante_principal")
            if idx_cuadrante < 0:
                return

            # Asegurar modo edición
            if not self.layer.isEditable():
                if not self.layer.startEditing():
                    QgsMessageLog.logMessage(
                        "[VIAL] No se pudo iniciar edición para guardar cuadrante_principal.",
                        "VIAL",
                        Qgis.Critical
                    )
                    return

            ok = self.layer.changeAttributeValue(fid, idx_cuadrante, new_code)

            # Si hay selección múltiple, aplicar el mismo valor a todos los seleccionados
            self._apply_value_to_selected(idx_cuadrante, new_code, fid)

            # Forzar refresco de la celda en la tabla (visual)
            try:
                src_index = self._source_index(index)
                self.attr_model.dataChanged.emit(
                    src_index, src_index, [Qt.DisplayRole, Qt.EditRole]
                )
            except Exception:
                pass

            return
        
        if field_name == "cuadrante_generadora" and isinstance(editor, QComboBox):
            if self.layer is None:
                return

            fid = self._fid_for_index(index)
            if fid is None:
                return

            new_code = editor.currentData()
            # Permitir None como valor válido (sin asignar)

            idx_cuadrante = self.layer.fields().indexOf("cuadrante_generadora")
            if idx_cuadrante < 0:
                return

            # Asegurar modo edición
            if not self.layer.isEditable():
                if not self.layer.startEditing():
                    QgsMessageLog.logMessage(
                        "[VIAL] No se pudo iniciar edición para guardar cuadrante_generadora.",
                        "VIAL",
                        Qgis.Critical
                    )
                    return

            ok = self.layer.changeAttributeValue(fid, idx_cuadrante, new_code)

            # Si hay selección múltiple, aplicar el mismo valor a todos los seleccionados
            self._apply_value_to_selected(idx_cuadrante, new_code, fid)

            # Forzar refresco de la celda en la tabla (visual)
            try:
                src_index = self._source_index(index)
                self.attr_model.dataChanged.emit(
                    src_index, src_index, [Qt.DisplayRole, Qt.EditRole]
                )
            except Exception:
                pass

            return

        # Flujo estándar para el resto de campos
        # Antes de guardar, convertir cadenas vacías a None
        if isinstance(editor, QLineEdit):
            text = editor.text().strip()
            if text == "":
                # Guardar como NULL en lugar de cadena vacía
                fid = self._fid_for_index(index)
                if fid is not None and self.layer is not None and field_name:
                    field_idx = self.layer.fields().indexOf(field_name)
                    if field_idx >= 0:
                        if not self.layer.isEditable():
                            self.layer.startEditing()
                        self.layer.changeAttributeValue(fid, field_idx, None)
                        # Actualizar visual
                        try:
                            src_index = self._source_index(index)
                            self.attr_model.dataChanged.emit(
                                src_index, src_index, [Qt.DisplayRole, Qt.EditRole]
                            )
                        except Exception:
                            pass
                        # No llamar a super() porque ya guardamos
                        # Continuar con la lógica de selección múltiple
                        fid_saved = fid
                        field_idx_saved = field_idx
                        # Si hay selección múltiple, aplicar None al resto
                        self._apply_value_to_selected(field_idx_saved, None, fid_saved)
                        return
        
        super().setModelData(editor, model, index)

        # Si hay selección múltiple, aplicar el valor editado al resto
        if self.layer is None or not field_name:
            return

        fid = self._fid_for_index(index)
        if fid is None:
            return

        selected_fids = list(self.layer.selectedFeatureIds())
        if len(selected_fids) <= 1 or fid not in selected_fids:
            return

        field_idx = self.layer.fields().indexOf(field_name)
        if field_idx < 0:
            return

        feat = self.layer.getFeature(fid)
        if not feat.isValid():
            return

        new_value = feat[field_idx]
        self._apply_value_to_selected(field_idx, new_value, fid)


class AttrEditorDock(QDockWidget):
    def __init__(self, iface, layer, parent=None, plugin=None):
        super().__init__("✎ VIAL — Editor de Nomenclatura Vial", parent)
        self.iface = iface
        self.layer = layer
        self.plugin = plugin

        # Asegurar edición para que los cambios en la tabla se guarden
        if self.layer and not self.layer.isEditable():
            if not self.layer.startEditing():
                self.iface.messageBar().pushMessage(
                    "VIAL",
                    "No se pudo iniciar la edición de la capa. "
                    "Revisa si la capa es editable.",
                    Qgis.Critical,
                    6,
                )
        # Índices de campos VIAL con autollenado
        self._idx_nombre_via = self.layer.fields().indexOf("nombre_via") \
            if self.layer and self.layer.fields().indexOf("nombre_via") != -1 else -1
        self._idx_tipo_via = self.layer.fields().indexOf("tipo_via") \
            if self.layer and self.layer.fields().indexOf("tipo_via") != -1 else -1
        self._idx_numero_via = self.layer.fields().indexOf("numero_via") \
            if self.layer and self.layer.fields().indexOf("numero_via") != -1 else -1
        self._idx_letra_principal = self.layer.fields().indexOf("letra_principal") \
            if self.layer and self.layer.fields().indexOf("letra_principal") != -1 else -1
        self._idx_prefijo_principal = self.layer.fields().indexOf("prefijo_principal") \
            if self.layer and self.layer.fields().indexOf("prefijo_principal") != -1 else -1
        self._idx_letra_prefijo_principal = self.layer.fields().indexOf("letra_prefijo_principal") \
            if self.layer and self.layer.fields().indexOf("letra_prefijo_principal") != -1 else -1
        self._idx_acto_admin = self.layer.fields().indexOf("acto_admin") \
            if self.layer and self.layer.fields().indexOf("acto_admin") != -1 else -1
        self._idx_nombre_popular = self.layer.fields().indexOf("nombre_popular") \
            if self.layer and self.layer.fields().indexOf("nombre_popular") != -1 else -1
        self._idx_cuadrante_principal = self.layer.fields().indexOf("cuadrante_principal") \
            if self.layer and self.layer.fields().indexOf("cuadrante_principal") != -1 else -1

        # Campo de fecha/hora del último cambio
        self._idx_fecha_cambio = self.layer.fields().indexOf("fecha_cambio") \
            if self.layer and self.layer.fields().indexOf("fecha_cambio") != -1 else -1

        # Campo para almacenar el histórico del segmento antes del último cambio
        # Se almacenará como un JSON compacto con los campos VIAL.
        self._idx_historico_nom = self.layer.fields().indexOf("historico_nom") \
            if self.layer and self.layer.fields().indexOf("historico_nom") != -1 else -1

        # Campos que activan la detección de calles contiguas
        # (centralizados en vial_reglas.py)
        self._watched_attr_idxs = get_watched_attr_idxs(self.layer)


        # Campos de salida para vía generadora
        self._idx_num_generadora = self.layer.fields().indexOf("num_generadora") \
            if self.layer and self.layer.fields().indexOf("num_generadora") != -1 else -1
        self._idx_letra_generadora = self.layer.fields().indexOf("letra_generadora") \
            if self.layer and self.layer.fields().indexOf("letra_generadora") != -1 else -1
        self._idx_sufijo_generadora = self.layer.fields().indexOf("sufijo_generadora") \
            if self.layer and self.layer.fields().indexOf("sufijo_generadora") != -1 else -1
        self._idx_letra_sufijo_generadora = self.layer.fields().indexOf("letra_sufijo_generadora") \
            if self.layer and self.layer.fields().indexOf("letra_sufijo_generadora") != -1 else -1
        self._idx_cuadrante_generadora = self.layer.fields().indexOf("cuadrante_generadora") \
            if self.layer and self.layer.fields().indexOf("cuadrante_generadora") != -1 else -1
        self._idx_tipo_via_generadora = self.layer.fields().indexOf("tipo_via_generadora") \
            if self.layer and self.layer.fields().indexOf("tipo_via_generadora") != -1 else -1


        # Almacenará las sugerencias de calles contiguas
        # Cada elemento: dict con keys:
        #   chain_id, name, chain_fids, change_fids, total, already_named, has_conflict
        self._contiguous_suggestions = []
        # Exclusiones por cadena: chain_id -> set(fids que NO se deben actualizar)
        self._chain_exclusions = {}

        # Estado para la tabla de detalles
        self._current_details_chain_id = None
        self._populating_details = False

        # --- Modo del panel de detalles ---
        # "chain" para detalles de cadena, "vg_review" para revisión de vía generadora (TV/DG)
        self._details_mode = None

        # Items en revisión de vía generadora (solo casos TV/DG)
        self._vg_review_items = []     # list[dict]
        self._vg_review_exclusions = set()  # set(base_fid) no aplicar

        # Flag: el usuario ha editado al menos un 'nombre_via' en esta sesión
        self._has_user_edited_nombre_via = False

        # FIDs modificados por el usuario desde la última detección
        self._dirty_fids = set()

        # Evita que los cambios automáticos del plugin disparen lógica de dirty/sugerencias/validación
        self._suppress_attr_changed = False

        # Estado: si estamos mostrando conflictos globales
        self._global_conflicts_mode = False


        # Flags para evitar bucles al sincronizar selección
        self._block_table_selection_slot = False
        self._block_layer_selection_slot = False
        self._syncing_from_layer = False
        self._syncing_from_table = False

        self.setWindowTitle(f"✎ VIAL — Editor de Nomenclatura — {layer.name()}")

        main = QWidget(self)
        self.setWidget(main)
        layout = QVBoxLayout(main)

        # ------------------------------------------------------------------
        # ENCABEZADO: barra de herramientas (volver, guardar, selección) + filtro
        # ------------------------------------------------------------------
        header = QHBoxLayout()

        self.btn_back_mapping = QToolButton()
        self.btn_back_mapping.setIcon(
            QgsApplication.getThemeIcon("/mActionArrowLeft.svg")
        )
        self.btn_back_mapping.setIconSize(QSize(18, 18))
        self.btn_back_mapping.setToolTip("Volver al mapeo de campos. (Descartar cambios si no has guardado)")

        self.btn_sel_all = QToolButton()
        self.btn_sel_all.setIcon(
            QgsApplication.getThemeIcon("/mActionSelectAll.svg")
        )
        self.btn_sel_all.setToolTip("Seleccionar todos los segmentos viales en la tabla")

        self.btn_sel_invert = QToolButton()
        self.btn_sel_invert.setIcon(
            QgsApplication.getThemeIcon("/mActionInvertSelection.svg")
        )
        self.btn_sel_invert.setToolTip("Invertir la selección (seleccionar los deseleccionados)")

        self.btn_sel_clear = QToolButton()
        self.btn_sel_clear.setIcon(
            QgsApplication.getThemeIcon("/mActionDeselectActiveLayer.svg")
        )
        self.btn_sel_clear.setToolTip("Deseleccionar todos los segmentos")

        self.btn_sel_top = QToolButton()
        self.btn_sel_top.setCheckable(True)
        self.btn_sel_top.setIcon(
            QgsApplication.getThemeIcon("/mActionSelectedToTop.svg")
        )
        self.btn_sel_top.setToolTip("Mostrar segmentos seleccionados al inicio de la tabla (facilita edición en lote)")

        self.btn_pan_selected = QToolButton()
        self.btn_pan_selected.setIcon(
            QgsApplication.getThemeIcon("/mActionPanToSelected.svg")
        )
        self.btn_pan_selected.setToolTip("Desplazar el mapa para centrar en los segmentos seleccionados")
        self.btn_pan_selected.setEnabled(False)

        self.btn_zoom_selected = QToolButton()
        self.btn_zoom_selected.setIcon(
            QgsApplication.getThemeIcon("/mActionZoomToSelected.svg")
        )
        self.btn_zoom_selected.setToolTip("Zoom al área que contiene los segmentos seleccionados")
        self.btn_zoom_selected.setEnabled(False)

        header.addWidget(self.btn_back_mapping)
        header.addSpacing(8)
        header.addWidget(self.btn_sel_all)
        header.addWidget(self.btn_sel_invert)
        header.addWidget(self.btn_sel_clear)
        header.addWidget(self.btn_sel_top)
        header.addWidget(self.btn_pan_selected)
        header.addWidget(self.btn_zoom_selected)
        header.addStretch(1)

        layout.addLayout(header)

        # ------------------------------------------------------------------
        # TABLA PRINCIPAL
        # ------------------------------------------------------------------
        self.table = QTableView()
        self.layer_cache = QgsVectorLayerCache(layer, 10000, self)

        self.model = QgsAttributeTableModel(self.layer_cache, self)
        self.model.loadLayer()
        
        # Configurar el modelo para que use los alias en los headers
        self.model.setRequest(self.model.request())  # Forzar refresh de configuración

        # IMPORTANTE: Crear QgsAttributeTableFilterModel pasando el modelo EN el constructor.
        # NO llamar a setSourceModel() después porque eso causa duplicación de columnas.
        self.proxy = QgsAttributeTableFilterModel(
            self.iface.mapCanvas(), self.model, self
        )
        # NO hacer: self.proxy.setSourceModel(self.model)  # Esto causa duplicación
        
        self.table.setModel(self.proxy)
        
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)  # Seleccionar CELDAS, no filas
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection) 
        self.table.setFocusPolicy(Qt.StrongFocus)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.SelectedClicked
            | QAbstractItemView.EditKeyPressed
        )
        # Delegado personalizado: respeta la lógica de QGIS pero añade combo para tipo_via
        self.table_delegate = VialAttributeDelegate(
            parent=self.table,
            layer=self.layer,
            attr_model=self.model,
            proxy_model=self.proxy,
            iface=self.iface,
        )
        self.table.setItemDelegate(self.table_delegate)

        layout.addWidget(self.table)
        self._configure_visible_columns()
        
        # Forzar que la tabla use los alias de los campos en los headers
        self._refresh_table_headers()
        self._apply_canonical_column_order()


        # ------------------------------------------------------------------
        # LÍNEA BAJO LA TABLA: dropdown vista + botones de acciones
        # ------------------------------------------------------------------
        self.btn_calc_via_generadora = QPushButton("Calcular vía generadora")
        self.btn_calc_via_generadora.setEnabled(False)
        self.btn_calc_via_generadora.setToolTip(
            "Detecta la vía generadora (origen) de segmentos que comparten atributos."
        )

        self.btn_identificar_contiguas = QPushButton("Identificar calles contiguas")
        self.btn_identificar_contiguas.setEnabled(True)  
        self.btn_identificar_contiguas.setToolTip(
            "Identifica cadenas contiguas.\n"
            "- Si no has editado nada recientemente: muestra conflictos globales y cadenas incompletas.\n"
            "- Si has editado segmentos: prioriza las cadenas tocadas (dirty)."
)
        self.view_mode_combo = QComboBox()
        self.view_mode_combo.addItem(
            "Mostrar todas las entidades",
            QgsAttributeTableFilterModel.ShowAll,
        )
        self.view_mode_combo.addItem(
            "Mostrar solo seleccionadas",
            QgsAttributeTableFilterModel.ShowSelected,
        )
        self.view_mode_combo.addItem(
            "Mostrar solo visibles en el mapa",
            QgsAttributeTableFilterModel.ShowVisible,
        )

        linea_vista = QHBoxLayout()
        linea_vista.addWidget(self.view_mode_combo)
        linea_vista.addStretch(1)
        linea_vista.addWidget(self.btn_identificar_contiguas)
        linea_vista.addWidget(self.btn_calc_via_generadora)
        layout.addLayout(linea_vista)


        # ------------------------------------------------------------------
        # PANEL DE SUGERENCIAS
        # ------------------------------------------------------------------
        sugg_label = QLabel(
            "<b>🔗 Sugerencias de Cadenas Viales</b><br>"
            "<small>Grupos de segmentos geométricamente contiguos y alineados. "
            "Haz clic en una cadena para ver detalles y aplicar cambios.</small>"
        )
        sugg_label.setWordWrap(True)
        layout.addWidget(sugg_label)
        self.sugg_table = QTableView()
        self.sugg_table.setAlternatingRowColors(True)
        self.sugg_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sugg_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.sugg_table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        # Modelo para las sugerencias
        self.sugg_model = QStandardItemModel(self)
        self._reset_suggestions_model()
        self.sugg_table.setModel(self.sugg_model)

        layout.addWidget(self.sugg_table)

        # --- Panel de detalles de la cadena seleccionada ---
        self.details_group = QGroupBox("📋 Detalles de Cadena Seleccionada")
        self.details_group.setVisible(False)

        details_layout = QVBoxLayout(self.details_group)

        info_label = QLabel(
            "<b>Verde</b>: campos que se actualizarán al valor sugerido.<br>"
            "<b>Aplicar</b>: marca cuáles segmentos deben recibir esta sugerencia.<br>"
            "<small>Nota: La sugerencia se aplica solo a los segmentos marcados.</small>"
        )
        info_label.setWordWrap(True)
        info_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        # --- Fila superior: texto + botón X ---
        header_details_layout = QHBoxLayout()
        header_details_layout.addWidget(info_label)

        self.btn_close_details = QToolButton()

        # Usar el mismo icono estándar que la X de los docks de QGIS
        icon = self.style().standardIcon(QStyle.SP_TitleBarCloseButton)
        self.btn_close_details.setIcon(icon)

        self.btn_close_details.setToolTip("Cerrar detalles de la cadena")
        header_details_layout.addWidget(self.btn_close_details)


        details_layout.addLayout(header_details_layout)

        self.details_table = QTableView()
        self.details_table.setAlternatingRowColors(True)
        self.details_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.details_table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        self.details_model = QStandardItemModel(self)
        self.details_table.setModel(self.details_model)

        details_layout.addWidget(self.details_table)

        layout.addWidget(self.details_group)


        sugg_buttons = QHBoxLayout()
        self.btn_apply_selected = QPushButton("✓ Aplicar sugerencias seleccionadas")
        self.btn_apply_selected.setToolTip(
            "Aplica la sugerencia de la cadena seleccionada solo a los segmentos "
            "que hayas marcado en la columna 'Aplicar'."
        )
        self._apply_selected_tooltip = self.btn_apply_selected.toolTip()
        self.btn_apply_all = QPushButton("✓ Aceptar todas las sugerencias")
        self.btn_apply_all.setToolTip(
            "Aplica TODAS las sugerencias a todos los segmentos de sus cadenas "
            "(sin necesidad de marcar segmentos individuales)."
        )
        self._apply_all_tooltip = self.btn_apply_all.toolTip()
        self.btn_clear_sugg = QPushButton("✕ Limpiar sugerencias")
        self.btn_clear_sugg.setToolTip(
            "Descarta todas las sugerencias. Vuelve a usar 'Identificar calles contiguas' "
            "para recalcular."
        )

        for b in (
            self.btn_apply_selected,
            self.btn_apply_all,
            self.btn_clear_sugg,
        ):
            sugg_buttons.addWidget(b)

        sugg_buttons.addStretch(1)
        layout.addLayout(sugg_buttons)

        # Conectar selección de la tabla de sugerencias a la selección en el mapa
        if self.sugg_table.selectionModel():
            self.sugg_table.selectionModel().selectionChanged.connect(
                self._on_suggestion_selection_changed
            )

        # Doble clic en una sugerencia -> mostrar detalles en el panel inferior
        self.sugg_table.doubleClicked.connect(self._on_suggestion_double_clicked)

        # Escuchar cambios en checkboxes de la tabla de detalles
        self.details_model.itemChanged.connect(self._on_details_item_changed)

        # Click en tabla de detalles -> seleccionar en mapa (especialmente útil para vg_review)
        self.details_table.clicked.connect(self._on_details_table_clicked)

        # ------------------------------------------------------------------
        # BARRA INFERIOR: solo botón Cerrar
        # ------------------------------------------------------------------
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.btn_close = QPushButton("Cerrar")
        bottom.addWidget(self.btn_close)
        layout.addLayout(bottom)

        # ------------------------------------------------------------------
        # CONEXIONES
        # ------------------------------------------------------------------
        self.view_mode_combo.currentIndexChanged.connect(self._on_view_mode_changed)

        self.btn_close.clicked.connect(self.close)
        self.btn_back_mapping.clicked.connect(self._on_back_to_mapping_clicked)

        self.btn_sel_all.clicked.connect(self.layer.selectAll)
        self.btn_sel_invert.clicked.connect(self.layer.invertSelection)
        self.btn_sel_clear.clicked.connect(self.layer.removeSelection)
        self.btn_sel_top.toggled.connect(self._on_selected_to_top_toggled)
        self.btn_identificar_contiguas.clicked.connect(self._on_identificar_contiguas_clicked)
        self.btn_close_details.clicked.connect(self._on_close_details_clicked)


        act_pan = self.iface.actionPanToSelected()
        if act_pan:
            self.btn_pan_selected.clicked.connect(act_pan.trigger)
        else:
            self.btn_pan_selected.setEnabled(False)

        act_zoom = self.iface.actionZoomToSelected()
        if act_zoom:
            self.btn_zoom_selected.clicked.connect(act_zoom.trigger)
        else:
            self.btn_zoom_selected.setEnabled(False)

        self.btn_apply_selected.clicked.connect(self._on_apply_selected_suggestions)
        self.btn_apply_all.clicked.connect(self._on_apply_all_suggestions)
        self.btn_clear_sugg.clicked.connect(self._on_clear_suggestions)
        self.btn_calc_via_generadora.clicked.connect(self._on_calc_via_generadora_clicked)

        # Sincronización bidireccional simplificada:
        # - Canvas → tabla: resalta solo la columna ID (columna 0)
        # - Tabla → canvas: se hace en _on_table_clicked
        self.layer.selectionChanged.connect(self._on_layer_selection_changed)
        self.layer.attributeValueChanged.connect(self._on_layer_attribute_changed)

        self.table.clicked.connect(self._on_table_clicked)

        self._update_selection_buttons()

        # Estado inicial del botón de vía generadora
        self._update_calc_via_generadora_state()

        # Estado inicial de acciones de sugerencias
        self._set_suggestions_action_state(is_global_conflicts=False)
        
    # ---------- Configuración de columnas visibles ----------

    def _reset_suggestions_model(self):
        """Reinicia el modelo de sugerencias con cabeceras estándar."""
        self.sugg_model.clear()
        self.sugg_model.setHorizontalHeaderLabels([
            "Cadena",            # chain_id
            "Nombre sugerido",   # nombre de vía
            "Tramos cadena",     # total de segmentos en esa cadena
            "Tramos a renombrar",# cuántos cambiarían
            "Estado",            # OK / conflicto
        ])

    def _set_suggestions_action_state(self, is_global_conflicts: bool):
        """
        Deshabilita acciones masivas cuando se revisan conflictos globales.
        """
        self._global_conflicts_mode = bool(is_global_conflicts)

        if self._global_conflicts_mode:
            self.btn_apply_selected.setEnabled(False)
            self.btn_apply_all.setEnabled(False)
            self.btn_apply_selected.setToolTip(
                "Desactivado en revision de conflictos globales."
            )
            self.btn_apply_all.setToolTip(
                "Desactivado en revision de conflictos globales."
            )
        else:
            self.btn_apply_selected.setEnabled(True)
            self.btn_apply_all.setEnabled(True)
            self.btn_apply_selected.setToolTip(self._apply_selected_tooltip)
            self.btn_apply_all.setToolTip(self._apply_all_tooltip)

    def _configure_visible_columns(self):
        """
        Oculta todas las columnas que no pertenezcan a los campos VIAL
        definidos en REQUIRED_FIELDS, pero conserva SIEMPRE la columna 0
        (ID/FID) para que la selección tabla/canvas funcione bien.
        """
        vial_names = {spec["name"] for spec in REQUIRED_FIELDS}

        keep_source_cols = set()

        src_col_count = self.model.columnCount(QModelIndex())
        for src_col in range(src_col_count):
            # 0 = columna de ID/FID -> siempre visible
            if src_col == 0:
                keep_source_cols.add(src_col)
                continue

            field_idx = self.model.fieldIdx(src_col)
            if field_idx < 0:
                continue

            field_name = self.layer.fields()[field_idx].name()
            if field_name in vial_names:
                keep_source_cols.add(src_col)

        proxy_col_count = self.proxy.columnCount(QModelIndex())
        for proxy_col in range(proxy_col_count):
            src_col = proxy_col
            keep = src_col in keep_source_cols
            self.table.setColumnHidden(proxy_col, not keep)

    def _refresh_table_headers(self):
        """
        Fuerza que los headers de la tabla muestren los alias de los campos
        en lugar de los nombres internos.
        """
        # Recargar completamente el modelo para que reconozca los alias
        self.model.loadLayer()

    def _apply_canonical_column_order(self):
        """
        Reordena visualmente las columnas del QTableView para que sigan
        el orden canónico definido en core.REQUIRED_FIELDS.

        OJO: esto es SOLO visual (header). No cambia el orden real de la capa.
        """
        if not self.layer:
            return

        header = self.table.horizontalHeader()

        # Orden canónico basado en REQUIRED_FIELDS
        canonical = [spec["name"] for spec in REQUIRED_FIELDS]

        # Mapa field_name -> source_col (columna del QgsAttributeTableModel)
        name_to_sourcecol = {}
        src_col_count = self.model.columnCount(QModelIndex())

        for src_col in range(src_col_count):
            if src_col == 0:
                continue  # ID/FID
            field_idx = self.model.fieldIdx(src_col)
            if field_idx < 0:
                continue
            fname = self.layer.fields()[field_idx].name()
            name_to_sourcecol[fname] = src_col

        # Objetivo: [0] + canonical (solo los que existen)
        target_source_cols = [0] + [name_to_sourcecol[n] for n in canonical if n in name_to_sourcecol]

        # Como tu proxy no reordena columnas, proxy_col == src_col.
        # Si en algún setup cambia, aquí habría que mapear.
        for target_pos, src_col in enumerate(target_source_cols):
            current_pos = header.visualIndex(src_col)
            if current_pos != target_pos and current_pos >= 0:
                header.moveSection(current_pos, target_pos)


    # ---------- Utilidades selección / modelo ----------
    def _on_table_clicked(self, proxy_index: QModelIndex):
        """
        Click en tabla => seleccionar la fila en canvas (sin forzar selección en tabla).
        Permite que el usuario edite cualquier celda sin que se reseleccione automáticamente.
        """
        if not proxy_index.isValid() or not self.layer:
            return

        # NO forzar selectRow() aquí - dejar que Qt maneje la selección de celda
        # (Ahora con SelectItems, el usuario puede hacer clic en cualquier celda)

        # Convertir proxy->source y obtener fid real con rowToId()
        src_index = self.proxy.mapToSource(proxy_index)
        if not src_index.isValid():
            return

        fid = self.model.rowToId(src_index.row())
        if fid is None or fid < 0:
            return

        # Si ya hay selección múltiple y este FID está dentro, no colapsar la selección
        selected_fids = set(self.layer.selectedFeatureIds())
        if len(selected_fids) > 1 and int(fid) in selected_fids:
            self._update_selection_buttons()
            return

        # Seleccionar en capa (canvas) - pero NO reselectar en tabla
        self._block_layer_selection_slot = True
        try:
            if self.iface:
                self.iface.setActiveLayer(self.layer)

            self.layer.selectByIds([int(fid)])
            self.layer.triggerRepaint()
            if self.iface:
                self.iface.mapCanvas().refresh()
        finally:
            self._block_layer_selection_slot = False

        self._update_selection_buttons()

    def _selected_feature_ids(self):
        """
        Devuelve FIDs seleccionados. Robusto a estilos donde selectedRows(0) no retorna nada.
        """
        sel_model = self.table.selectionModel()
        if not sel_model:
            return []

        fids = set()

        # 1) Intento estándar: selectedRows
        rows = sel_model.selectedRows()
        if not rows:
            # 2) Fallback: usar selectedIndexes y tomar filas únicas
            rows = []
            for idx in sel_model.selectedIndexes():
                if idx.isValid():
                    rows.append(idx)

        for proxy_index in rows:
            if not proxy_index.isValid():
                continue

            src_index = self.proxy.mapToSource(proxy_index)
            if not src_index.isValid():
                continue

            fid = self.model.rowToId(src_index.row())
            if fid is None or fid < 0:
                continue

            fids.add(int(fid))

        return list(fids)

    
    def _update_selection_buttons(self):
        sel_model = self.table.selectionModel()
        has_selection = bool(sel_model and sel_model.hasSelection())
        self.btn_pan_selected.setEnabled(has_selection)
        self.btn_zoom_selected.setEnabled(has_selection)
        self.btn_sel_top.setEnabled(has_selection)

    def _update_calc_via_generadora_state(self):
        """
        Activa el botón 'Calcular vía generadora' solo cuando:
        - existe el campo 'numero_via' y
        - TODAS las entidades tienen un valor numérico válido en ese campo.
        """
        if not self.layer or self._idx_numero_via < 0:
            self.btn_calc_via_generadora.setEnabled(False)
            self.btn_calc_via_generadora.setToolTip(
                "La capa no tiene el campo 'numero_via'; no se puede calcular la vía generadora."
            )
            return

        all_ok = True
        for f in self.layer.getFeatures():
            val = f[self._idx_numero_via]
            if val is None:
                all_ok = False
                break
            text = str(val).strip()
            if (not text) or text.upper() == "NULL" or (not text.isdigit()):
                all_ok = False
                break

        self.btn_calc_via_generadora.setEnabled(all_ok)
        if all_ok:
            self.btn_calc_via_generadora.setToolTip(
                "Calcular 'vía generadora' para todos los segmentos."
            )
        else:
            self.btn_calc_via_generadora.setToolTip(
                "Se habilita cuando todos los segmentos tienen 'numero_via' numérico y distinto de NULL."
            )


    def _on_layer_attribute_changed(self, fid, idx, value):
        """
        Se llama cuando cambia algún atributo de la capa.

        - Normaliza silenciosamente con FIELD_VALIDATORS (sin mostrar warnings).
        - Si es watched field y queda no-vacío, activa botón y marca dirty.

        NOTA: Los warnings de validación se muestran solo en el delegate (setModelData)
        cuando el usuario edita una celda. Aquí solo normalizamos sin mostrar warnings
        para evitar alertas en carga de datos, rollback, etc.

        IMPORTANTÍSIMO:
        - self._dirty_fids guarda SIEMPRE QGIS feature.id() (el fid del evento),
        NO el atributo 'fid' de la tabla.
        """
        if not self.layer:
            return

        fields = self.layer.fields()
        if idx < 0 or idx >= len(fields):
            return

        # Si el cambio lo está haciendo el plugin (aplicar sugerencias / normalizaciones internas),
        # no ejecutes lógica de validación/dirty/botones, pero deja que QGIS actualice la tabla.
        if getattr(self, "_suppress_attr_changed", False):
            return

        field_name = fields[idx].name()

        # 1) Normalización silenciosa por campo (sin mostrar warnings)
        validator = FIELD_VALIDATORS.get(field_name)
        if validator is not None:
            normalized, error_msg = validator(value)

            # Si hay error, limpiar silenciosamente (sin warning)
            if error_msg:
                if value is not None and str(value).strip() != "":
                    self._suppress_attr_changed = True
                    try:
                        self.layer.changeAttributeValue(fid, idx, None)
                    finally:
                        self._suppress_attr_changed = False
                # No seguimos con dirty/botón si quedó inválido/vacío
                return

            # Valor válido: si hay normalización, reescribimos el valor normalizado silenciosamente
            if normalized != value:
                self._suppress_attr_changed = True
                try:
                    self.layer.changeAttributeValue(fid, idx, normalized)
                finally:
                    self._suppress_attr_changed = False

                value = normalized  # importante: seguir el flujo con el valor final

            # Regla especial: si cambia numero_via, actualizar botón
            if field_name == "numero_via":
                self._update_calc_via_generadora_state()

        # 2) Solo watched fields disparan sugerencias
        if idx not in self._watched_attr_idxs:
            return

        # 3) Activar sugerencias si el usuario tocó un watched field (incluye borrado)
        text = "" if value is None else str(value).strip()
        if text.upper() == "NULL":
            text = ""

        self._has_user_edited_nombre_via = True
        self.btn_identificar_contiguas.setEnabled(True)

        # ✅ Dirty = QGIS fid (estable en sesión)
        self._dirty_fids.add(int(fid))



    def _build_contiguous_suggestions(self, chain_layer, dirty_ids=None):
        """
        Construye sugerencias por cadena.

        dirty_ids:
        - Con esta versión, dirty_ids debe ser SIEMPRE set/list de QGIS fids.
        - Se filtran cadenas que contengan alguno de esos QGIS fids.
        """

        if not self.layer or not chain_layer:
            return

        idx_chain = chain_layer.fields().indexOf("chain_id")
        if idx_chain < 0:
            self.iface.messageBar().pushMessage(
                "VIAL",
                "La capa de cadenas no tiene 'chain_id'.",
                Qgis.Critical,
                5,
            )
            return
        
        # Identificador que trae la chain_layer
        # Preferimos 'fid', si no existe probamos '__orig_fid', luego 'id'
        idx_key = chain_layer.fields().indexOf("__orig_fid")
        if idx_key == -1:
            idx_key = chain_layer.fields().indexOf("fid")
        if idx_key == -1:
            idx_key = chain_layer.fields().indexOf("id")

        
        key_field_name = chain_layer.fields()[idx_key].name() if idx_key >= 0 else "NONE"

        if idx_key < 0:
            self.iface.messageBar().pushMessage(
                "VIAL",
                "La capa de cadenas no tiene un id ('fid'/'__orig_fid'/'id').",
                Qgis.Critical,
                5,
            )
            return

        # dirty_ids ahora ya son QGIS fids
        dirty_qgis = set(dirty_ids) if dirty_ids is not None else None

        # --- Fallback mapping: atributo 'fid' (PK) -> QGIS fid ---
        # Solo se usa si el valor de chain_layer no corresponde a un QGIS fid válido.
        idx_attr_fid = self.layer.fields().indexOf("fid")
        pk_to_qgisfid = {}
        if idx_attr_fid >= 0:
            try:
                for ft in self.layer.getFeatures():
                    v = ft[idx_attr_fid]
                    if v is None:
                        continue
                    t = str(v).strip()
                    if not t:
                        continue
                    pk_to_qgisfid[int(t)] = ft.id()
            except Exception:
                pk_to_qgisfid = {}

        def _to_qgis_fid(key_val):
            """
            key_val viene del chain_layer (puede ser QGIS fid o PK del campo 'fid').
            1) Si es un QGIS fid válido -> úsalo
            2) Si no, intenta mapear PK -> QGIS fid con pk_to_qgisfid
            """
            try:
                k = int(key_val)
            except Exception:
                return None

            # 1) ¿Es un QGIS fid válido?
            try:
                test = self.layer.getFeature(k)
                if test and test.isValid():
                    return k
            except Exception:
                pass

            # 2) Mapear como PK del atributo 'fid'
            return pk_to_qgisfid.get(k)

        # chain_id -> list(qgis_fids)
        chains = {}
        for f in chain_layer.getFeatures():
            chain_id = f[idx_chain]
            key_val = f[idx_key]
            if chain_id is None or key_val is None:
                continue
            try:
                chain_id = int(chain_id)
            except Exception:
                continue

            qfid = _to_qgis_fid(key_val)
            if qfid is None:
                continue

            chains.setdefault(chain_id, []).append(qfid)

        self._contiguous_suggestions = []
        self._reset_suggestions_model()

        field_cfg = [
            ("tipo_via", self._idx_tipo_via),
            ("nombre_via", self._idx_nombre_via),
            ("numero_via", self._idx_numero_via),
            ("letra_principal", self._idx_letra_principal),
            ("prefijo_principal", self._idx_prefijo_principal),
            ("letra_prefijo_principal", self._idx_letra_prefijo_principal),
            ("cuadrante_principal", self._idx_cuadrante_principal),  
            ("acto_admin", self._idx_acto_admin),
            ("nombre_popular", self._idx_nombre_popular),
        ]

        for chain_id, chain_fids in sorted(chains.items()):
            # Filtrar por dirty si aplica
            if dirty_qgis is not None:
                if not any(qfid in dirty_qgis for qfid in chain_fids):
                    continue

            chain_feats = []
            for qfid in chain_fids:
                ft = self.layer.getFeature(qfid)
                if ft and ft.isValid():
                    chain_feats.append(ft)

            if not chain_feats:
                continue

            field_results = {}
            any_nonempty_field = False
            any_changes = False
            any_conflict = False

            for field_name, field_idx in field_cfg:
                if field_idx < 0:
                    continue

                values = set()
                value_by_fid = {}

                for ft in chain_feats:
                    qfid = ft.id()
                    v = ft[field_idx]
                    txt = "" if v is None else str(v).strip()
                    if txt.upper() == "NULL":
                        txt = ""

                    if field_name == "numero_via" and txt and not txt.isdigit():
                        txt = ""

                    if not txt:
                        continue

                    values.add(txt)
                    value_by_fid[qfid] = txt

                if not values:
                    continue

                any_nonempty_field = True
                total = len(chain_fids)
                already_named = len(value_by_fid)

                if len(values) == 1:
                    suggested = next(iter(values))
                    change_fids = []

                    for ft in chain_feats:
                        qfid = ft.id()
                        cur = ft[field_idx]
                        cur_txt = "" if cur is None else str(cur).strip()
                        if cur_txt.upper() == "NULL":
                            cur_txt = ""
                        if field_name == "numero_via" and cur_txt and not cur_txt.isdigit():
                            cur_txt = ""

                        if cur_txt != suggested:
                            change_fids.append(qfid)

                    has_conflict_field = False
                    if change_fids:
                        any_changes = True

                else:
                    # ✅ NUEVO: por defecto, si hay múltiples valores => es conflicto (modo global)
                    # En modo dirty, esto es un "fallback" que puede ser resuelto por preferred.
                    suggested = ", ".join(sorted(values))
                    change_fids = []
                    has_conflict_field = True
                    preferred = None

                    # ✅ IMPORTANTE:
                    # - Si estamos en modo global (dirty_qgis is None), marcamos conflicto aquí.
                    # - Si estamos en modo dirty, dejamos que el bloque de abajo decida (preferred).
                    if dirty_qgis is None:
                        any_conflict = True

                # Resolver conflicto con dirty (si los dirty en esa cadena tienen un único valor,
                # INCLUYENDO el caso de "vacío" para permitir borrado propagable)
                if dirty_qgis is not None:
                    dirty_vals = set()
                    saw_dirty = False

                    for ft in chain_feats:
                        qfid = ft.id()
                        if qfid not in dirty_qgis:
                            continue

                        saw_dirty = True
                        v = ft[field_idx]
                        t = "" if v is None else str(v).strip()
                        if t.upper() == "NULL":
                            t = ""

                        # numero_via inválido -> lo tratamos como vacío
                        if field_name == "numero_via" and t and not t.isdigit():
                            t = ""

                        dirty_vals.add(t)

                    # Solo si hubo dirty en esa cadena y TODOS coinciden (incluido "" para borrado)
                    if saw_dirty and len(dirty_vals) == 1:
                        preferred = next(iter(dirty_vals))  # puede ser "" (borrado)

                    if preferred is not None:
                        suggested = preferred
                        change_fids = []

                        for ft in chain_feats:
                            qfid = ft.id()
                            cur = ft[field_idx]
                            cur_txt = "" if cur is None else str(cur).strip()
                            if cur_txt.upper() == "NULL":
                                cur_txt = ""
                            if field_name == "numero_via" and cur_txt and not cur_txt.isdigit():
                                cur_txt = ""
                            if cur_txt != suggested:
                                change_fids.append(qfid)

                        has_conflict_field = False
                        if change_fids:
                            any_changes = True
                    else:
                        # ✅ Si NO se pudo resolver con dirty => conflicto (también en modo dirty)
                        has_conflict_field = True
                        any_conflict = True
                        # suggested ya está como ", ".join(sorted(values)) y change_fids vacío

                field_results[field_name] = dict(
                    suggested=suggested,
                    change_fids=change_fids,
                    total=total,
                    already_named=already_named,
                    has_conflict=has_conflict_field,
                )

            if not any_nonempty_field:
                continue
            if not any_changes and not any_conflict:
                continue

            label_parts = []
            for fname in ("tipo_via", "nombre_via", "numero_via", "nombre_popular"):
                fr = field_results.get(fname)
                if not fr or fr.get("has_conflict"):
                    continue
                val = fr.get("suggested")
                if val:
                    label_parts.append(val)
            label = " ".join(label_parts) if label_parts else "(ver detalles)"

            conflict_fields = []
            change_summaries = []
            for fname, fr in field_results.items():
                if fr.get("has_conflict"):
                    conflict_fields.append(fname)
                n_change = len(fr.get("change_fids", []))
                if n_change > 0 and not fr.get("has_conflict"):
                    change_summaries.append(f"{fname} ({n_change})")

            if any_conflict:
                estado = "Conflicto en: " + ", ".join(conflict_fields)
                if change_summaries:
                    estado += " | Cambios en: " + ", ".join(change_summaries)
            else:
                estado = "OK – Cambios en: " + ", ".join(change_summaries) if change_summaries else "OK"

            total_tramos = len(chain_fids)
            tramos_a_renombrar = sum(len(fr.get("change_fids", [])) for fr in field_results.values())

            suggestion = dict(
                chain_id=chain_id,
                label=label,
                chain_fids=chain_fids,
                fields=field_results,
                total=total_tramos,
                has_conflict=any_conflict,
            )
            self._contiguous_suggestions.append(suggestion)

            row = [
                QStandardItem(str(chain_id)),
                QStandardItem(label),
                QStandardItem(str(total_tramos)),
                QStandardItem(str(tramos_a_renombrar)),
                QStandardItem(estado),
            ]
            row[0].setData(len(self._contiguous_suggestions) - 1, Qt.UserRole)
            for it in row:
                it.setEditable(False)
            self.sugg_model.appendRow(row)

        self.sugg_table.resizeColumnsToContents()


    def _make_chains_input_layer(self, dirty_ids=None):
        """
        Crea una capa temporal (memory) SOLO con lo necesario para construir cadenas:
        - geometría lineal normalizada a MultiLineString 2D
        - __orig_fid: fid real del layer original (QGIS feature.id())
        - id: entero (requerido por road_chain_merger) = __orig_fid

        ✅ FIX CLAVE:
        NO copiamos atributos originales (especialmente historico_nom),
        porque pueden exceder el límite de strings en capas temporales (255)
        y eso hace que QGIS "pierda" features al hacer addFeatures().
        """
        if not self.layer:
            return None

        dirty_set = set(dirty_ids) if dirty_ids else set()

        crs = self.layer.crs()
        uri = f"MultiLineString?crs={crs.authid()}"
        mem = QgsVectorLayer(uri, "vial_chains_input", "memory")
        if not mem.isValid():
            return None

        pr = mem.dataProvider()

        # ✅ SOLO los campos mínimos
        pr.addAttributes([
            QgsField("__orig_fid", QVariant.LongLong),
            QgsField("id", QVariant.Int),
        ])
        mem.updateFields()

        def _force_multiline_2d(g: QgsGeometry) -> QgsGeometry:
            if g is None or g.isEmpty():
                return None

            gg = QgsGeometry(g)  # copia

            # Drop Z/M
            try:
                gg.dropZValue()
            except Exception:
                pass
            try:
                gg.dropMValue()
            except Exception:
                pass

            # makeValid si existe
            try:
                mv = gg.makeValid()
                if mv is not None and not mv.isEmpty():
                    gg = mv
            except Exception:
                pass

            # Solo líneas
            try:
                gtype = QgsWkbTypes.geometryType(gg.wkbType())
                if gtype != QgsWkbTypes.LineGeometry:
                    return None
            except Exception:
                return None

            # Forzar a MultiLineString
            try:
                if QgsWkbTypes.isSingleType(gg.wkbType()):
                    pl = gg.asPolyline()
                    if not pl or len(pl) < 2:
                        return None
                    gg = QgsGeometry.fromMultiPolylineXY([pl])
                else:
                    mpl = gg.asMultiPolyline()
                    if not mpl:
                        return None
                    gg = QgsGeometry.fromMultiPolylineXY(mpl)
            except Exception:
                return None

            if gg is None or gg.isEmpty():
                return None
            return gg

        feats_to_add = []
        total_in = 0
        skipped_empty = 0
        skipped_not_line = 0

        for f in self.layer.getFeatures():
            total_in += 1
            fid = int(f.id())
            geom = f.geometry()

            if geom is None or geom.isEmpty():
                skipped_empty += 1
                continue

            norm = _force_multiline_2d(geom)
            if norm is None:
                skipped_not_line += 1
                continue

            nf = QgsFeature(mem.fields())
            nf.setGeometry(norm)
            # __orig_fid y id (para el algoritmo)
            nf.setAttributes([fid, fid])
            feats_to_add.append(nf)

        ok_bulk, _ = pr.addFeatures(feats_to_add)
        mem.updateExtents()

        # ✅ Verificar que los dirty están presentes
        if dirty_set:
            idx_orig = mem.fields().indexOf("__orig_fid")
            present = set()
            for ft in mem.getFeatures():
                try:
                    present.add(int(ft[idx_orig]))
                except Exception:
                    pass

            missing_dirty = sorted(dirty_set - present)
            QgsMessageLog.logMessage(
                f"[DEBUG] chains_input contains dirty? missing_dirty={missing_dirty}",
                "VIAL",
                Qgis.Info
            )

            if missing_dirty:
                self.iface.messageBar().pushMessage(
                    "VIAL",
                    f"No se pueden generar sugerencias: estos segmentos editados no entraron en chains_input: {missing_dirty}. "
                    f"Recomendación: revisa geometría del segmento (Fix Geometries) o simplifica.",
                    Qgis.Critical,
                    8,
                )

        return mem




    def _on_suggestion_selection_changed(self, selected, deselected):
        """
        Cuando el usuario selecciona una fila en la tabla de sugerencias,
        seleccionamos en el mapa todos los tramos de esa cadena para que
        pueda inspeccionar visualmente la contigüidad.
        """
        if not self._contiguous_suggestions:
            return

        indexes = selected.indexes()
        if not indexes:
            return

        # Tomamos la primera columna de la fila seleccionada
        idx = indexes[0]
        row = idx.row()
        # También podríamos usar el UserRole, pero aquí fila == índice
        if row < 0 or row >= len(self._contiguous_suggestions):
            return

        suggestion = self._contiguous_suggestions[row]
        fids = suggestion.get("chain_fids", [])

        try:
            self.layer.selectionChanged.disconnect(self._on_layer_selection_changed)
        except Exception:
            pass

        if fids:
            self.layer.selectByIds(fids)
        else:
            self.layer.removeSelection()

        self.layer.selectionChanged.connect(self._on_layer_selection_changed)
        self._update_selection_buttons()

    def _on_suggestion_double_clicked(self, index):
        """
        Al hacer doble clic en una fila de la tabla de sugerencias,
        mostramos en el panel inferior el detalle por tramo y campo.
        """
        if not index.isValid():
            return

        row = index.row()
        if row < 0 or row >= len(self._contiguous_suggestions):
            return

        suggestion = self._contiguous_suggestions[row]
        chain_id = suggestion.get("chain_id")

        self._populate_details_table(chain_id, suggestion)

    def _on_close_details_clicked(self):
        """
        Oculta el panel de detalles (cadena o revisión vía generadora) y limpia el estado actual.
        """
        self.details_group.setVisible(False)
        self._current_details_chain_id = None
        self._details_mode = None


    def _on_details_table_clicked(self, index: QModelIndex):
        """
        Click en detalles:
        - En modo 'chain': opcionalmente seleccionar el FID del tramo clickeado.
        - En modo 'vg_review': seleccionar base + generadora para inspección visual.
        """
        if not index.isValid() or not self.layer:
            return

        row = index.row()

        if self._details_mode == "vg_review":
            # Column 0 tiene el item checkable donde guardaremos base_fid y gen_fid
            apply_item = self.details_model.item(row, 0)
            if apply_item is None:
                return

            payload = apply_item.data(Qt.UserRole)  # dict {"base_fid":..., "gen_fid":...}
            if not isinstance(payload, dict):
                return

            base_fid = payload.get("base_fid")
            gen_fid = payload.get("gen_fid")

            ids = []
            if isinstance(base_fid, int):
                ids.append(base_fid)
            if isinstance(gen_fid, int):
                ids.append(gen_fid)

            if ids:
                try:
                    self.layer.selectByIds(ids)
                    self.layer.triggerRepaint()
                    if self.iface:
                        self.iface.mapCanvas().refresh()
                except Exception:
                    pass

            self._update_selection_buttons()
            return

        # Modo cadena: seleccionar solo el fid clickeado (si está disponible)
        if self._details_mode == "chain":
            apply_item = self.details_model.item(row, 0)
            if apply_item is None:
                return
            fid = apply_item.data(Qt.UserRole)  # en chain guardas fid (int)
            if fid is None:
                return
            try:
                self.layer.selectByIds([int(fid)])
                self.layer.triggerRepaint()
                if self.iface:
                    self.iface.mapCanvas().refresh()
            except Exception:
                pass
            self._update_selection_buttons()


    def _populate_details_table(self, chain_id, suggestion):
        """
        Rellena la tabla inferior con una fila por tramo de la cadena y
        una columna "Aplicar" (checkbox) para decidir dónde aplicar la
        sugerencia.
        """
        self._details_mode = "chain"
        self._current_details_chain_id = chain_id
        self.details_group.setTitle(f"Detalles de cadena {chain_id}")
        self.details_group.setVisible(True)

        excluded = {
            "historico_nom", "fecha_cambio",
            "num_generadora", "letra_generadora",
            "sufijo_generadora", "letra_sufijo_generadora",
            "cuadrante_generadora",
        }
        field_order = [spec["name"] for spec in REQUIRED_FIELDS if spec["name"] not in excluded]

        fields = self.layer.fields()
        name_to_idx = {name: fields.indexOf(name) for name in field_order}

        excluded = self._chain_exclusions.get(chain_id, set())
        field_results = suggestion.get("fields", {})
        chain_fids = suggestion.get("chain_fids", [])

        self._populating_details = True
        self.details_model.clear()

        # Cabeceras: Aplicar, FID, campos VIAL existentes
        headers = ["Aplicar", "FID"]
        for name in field_order:
            if name_to_idx[name] >= 0:
                headers.append(name)
        self.details_model.setHorizontalHeaderLabels(headers)

        for fid in chain_fids:
            feat = self.layer.getFeature(fid)
            if not feat.isValid():
                continue

            row_items = []

            # Columna 0: checkbox "Aplicar"
            apply_item = QStandardItem()
            apply_item.setCheckable(True)
            apply_item.setEditable(False)
            apply_item.setData(fid, Qt.UserRole)  # guardar fid

            if fid in excluded:
                apply_item.setCheckState(Qt.Unchecked)
            else:
                apply_item.setCheckState(Qt.Checked)

            row_items.append(apply_item)

            # Columna 1: FID
            fid_item = QStandardItem(str(fid))
            fid_item.setEditable(False)
            row_items.append(fid_item)

            # Resto de columnas: valores por campo
            for name in field_order:
                idx = name_to_idx[name]
                if idx < 0:
                    continue

                val = feat[idx]
                text = ""
                if val is not None:
                    text = str(val).strip()
                    if text.upper() == "NULL":
                        text = ""

                item = QStandardItem(text)
                item.setEditable(False)

                fr = field_results.get(name)
                if fr:
                    # Campo en conflicto -> amarillo
                    if fr.get("has_conflict"):
                        item.setBackground(QColor(255, 255, 180))  # amarillo suave

                    # Este FID se va a cambiar en este campo -> verde + negrita
                    if fid in fr.get("change_fids", []):
                        font = item.font()
                        font.setBold(True)
                        item.setFont(font)
                        item.setBackground(QColor(200, 255, 200))  # verde suave
                        suggested = fr.get("suggested")
                        if suggested:
                            item.setToolTip(f"Se cambiará a: {suggested}")

                row_items.append(item)

            self.details_model.appendRow(row_items)

        self.details_table.resizeColumnsToContents()
        self._populating_details = False

    def _populate_vg_review_table(self):
        """
        Muestra en el panel de detalles TODOS los casos donde la vía generadora
        calculada es TV o DG. El usuario decide con checkboxes si aplicar o no.
        
        Columnas mostradas:
        - Aplicar (checkbox)
        - Vía Principal (concatenación de campos base)
        - Vía Generadora Sugerida (sugerencia concatenada)
        """
        self._details_mode = "vg_review"
        self._current_details_chain_id = None  # no aplica aquí
        self.details_group.setTitle("📋 Revisión vía generadora (solo TV/DG)")
        self.details_group.setVisible(True)

        # Cabeceras
        self._populating_details = True
        self.details_model.clear()
        self.details_model.setHorizontalHeaderLabels([
            "Aplicar",
            "Vía Principal",
            "Vía Generadora Sugerida",
        ])

        for it in self._vg_review_items:
            # --- leer payload ---
            base_fid = it.get("base_fid")
            gen_fid = it.get("gen_fid")
            gen_tipo = it.get("gen_tipo", "") or ""

            base_num = it.get("base_num")
            base_letra = it.get("base_letra") or ""
            base_cuad = it.get("base_cuad") or ""

            out_num = it.get("out_num")
            out_letra = it.get("out_letra") or ""
            out_cuad = it.get("out_cuad") or ""
            out_suf = it.get("out_suf") or ""
            out_suf_letra = it.get("out_suf_letra") or ""

            # Col 0: checkbox "Aplicar"
            apply_item = QStandardItem()
            apply_item.setCheckable(True)
            apply_item.setEditable(False)

            # guardamos base+gen en UserRole para el click en mapa
            apply_item.setData(
                {
                    "base_fid": int(base_fid) if base_fid is not None else None,
                    "gen_fid": int(gen_fid) if gen_fid is not None else None,
                },
                Qt.UserRole
            )

            if base_fid in self._vg_review_exclusions:
                apply_item.setCheckState(Qt.Unchecked)
            else:
                apply_item.setCheckState(Qt.Checked)

            # --- Columna: Vía Principal (concatenada) ---
            via_principal_parts = []
            if base_num is not None:
                via_principal_parts.append(str(base_num))
            if base_letra:
                via_principal_parts.append(str(base_letra))
            if base_cuad:
                via_principal_parts.append(str(base_cuad))
            
            via_principal_text = " ".join(via_principal_parts)
            via_principal_item = QStandardItem(via_principal_text)
            via_principal_item.setEditable(False)

            # --- Columna: Vía Generadora Sugerida (concatenada) ---
            via_gen_parts = []
            if gen_tipo:
                via_gen_parts.append(str(gen_tipo))
            if out_num is not None:
                via_gen_parts.append(str(out_num))
            if out_letra:
                via_gen_parts.append(str(out_letra))
            if out_suf:
                via_gen_parts.append(str(out_suf))
            if out_suf_letra:
                via_gen_parts.append(str(out_suf_letra))
            if out_cuad:
                via_gen_parts.append(str(out_cuad))
            
            via_gen_text = " ".join(via_gen_parts)
            via_gen_item = QStandardItem(via_gen_text)
            via_gen_item.setEditable(False)
            via_gen_item.setBackground(QColor(200, 255, 200))  # verde suave

            # --- armar fila completa ---
            row_items = [
                apply_item,
                via_principal_item,
                via_gen_item,
            ]
            self.details_model.appendRow(row_items)

        self.details_table.resizeColumnsToContents()
        self._populating_details = False


    def _on_details_item_changed(self, item):
        if self._populating_details:
            return

        if item.column() != 0:
            return

        # --- Modo revisión vía generadora ---
        if self._details_mode == "vg_review":
            payload = item.data(Qt.UserRole)
            if not isinstance(payload, dict):
                return
            base_fid = payload.get("base_fid")
            if base_fid is None:
                return

            if item.checkState() == Qt.Checked:
                self._vg_review_exclusions.discard(base_fid)
            else:
                self._vg_review_exclusions.add(base_fid)
            return

        # --- Modo cadena (comportamiento actual) ---
        chain_id = self._current_details_chain_id
        if chain_id is None:
            return

        fid = item.data(Qt.UserRole)
        if fid is None:
            return

        excluded = self._chain_exclusions.setdefault(chain_id, set())
        if item.checkState() == Qt.Checked:
            excluded.discard(fid)
        else:
            excluded.add(fid)


    def _on_identificar_contiguas_clicked(self):
        """
        Calcula las cadenas de calles contiguas y genera sugerencias
        basadas en los valores ya introducidos por el usuario.

        Estrategia (robusta):
        - Construimos una capa temporal MINIMAL (chains_input) sin atributos largos.
        - Calculamos cadenas sobre chains_input.
        - Filtramos sugerencias por dirty (QGIS fids).
        """
        if not self.layer:
            return

        # Al menos uno de los campos VIAL debe existir para que tenga sentido
        if not any(idx >= 0 for idx in (
            self._idx_tipo_via,
            self._idx_nombre_via,
            self._idx_numero_via,
            self._idx_letra_principal,
            self._idx_prefijo_principal,
            self._idx_letra_prefijo_principal,
            self._idx_acto_admin,
            self._idx_nombre_popular,
        )):
            self.iface.messageBar().pushMessage(
                "VIAL",
                "La capa no tiene campos VIAL configurados; no se pueden generar sugerencias.",
                Qgis.Critical,
                5,
            )
            return

        # ✅ Filtrar sugerencias solo a cadenas tocadas por el usuario
        dirty_ids = self._dirty_fids if self._dirty_fids else None

        chain_layer = None
        chains_input = None

        try:
            QgsApplication.setOverrideCursor(Qt.WaitCursor)

            # ✅ construir capa temporal MINIMAL (sin atributos largos)
            chains_input = self._make_chains_input_layer(dirty_ids=dirty_ids)

            if (not chains_input) or (not chains_input.isValid()) or (chains_input.featureCount() == 0):
                self.iface.messageBar().pushMessage(
                    "VIAL",
                    "No se pudo construir la capa temporal para cadenas (chains_input).",
                    Qgis.Critical,
                    6,
                )
                return

            chain_layer = compute_chains_layer(
                chains_input,
                snap_tol_m=0.5,
                angle_thresh_deg=15.0,
            )

        except Exception as exc:
            self.iface.messageBar().pushMessage(
                "VIAL",
                f"Error al identificar cadenas contiguas: {exc}",
                Qgis.Critical,
                7,
            )
            return

        finally:
            QgsApplication.restoreOverrideCursor()

        # Construir sugerencias (filtradas por dirty_ids si aplica)
        self._build_contiguous_suggestions(chain_layer, dirty_ids=dirty_ids)

        # Si no hay dirty_ids, se asume revision global de conflictos
        self._set_suggestions_action_state(is_global_conflicts=(dirty_ids is None))

        # Consideramos estos cambios como “procesados”
        self._dirty_fids.clear()

        self.iface.messageBar().pushMessage(
            "VIAL",
            "Sugerencias de calles contiguas actualizadas.",
            Qgis.Info,
            4,
        )




    # ---------- Slots principales ----------

    def _on_back_to_mapping_clicked(self):
        """
        Volver al mapeo: pregunta si desea guardar cambios, descartar o cancelar.
        Si hay cambios, ofrece la opción de guardarlos antes de volver al mapeo.
        """
        if not getattr(self, "plugin", None):
            QMessageBox.warning(self, "VIAL", "No se pudo volver al mapeo: el plugin no está disponible.")
            return

        layer = self.layer
        if not layer:
            return

        # Preguntar qué hacer con los cambios pendientes
        if layer.isEditable() and layer.isModified():
            reply = QMessageBox.question(
                self,
                "VIAL — Cambios sin guardar",
                "Tienes cambios sin guardar en la nomenclatura vial.\n\n"
                "Si vuelves al mapeo de campos, los cambios se descartarán.\n\n"
                "¿Deseas guardar los cambios antes de volver?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Cancel
            )

            if reply == QMessageBox.Save:
                # Guardar antes de volver al mapeo
                if not layer.commitChanges():
                    QMessageBox.critical(
                        self,
                        "Error al guardar",
                        f"No se pudieron guardar los cambios de nomenclatura:\n{layer.commitErrors()}"
                    )
                    return
            elif reply == QMessageBox.Cancel:
                # No hacer nada, volver al dock
                return
            # Si es Discard, continuar con rollback

        # Revertir cambios del mapeo inicial
        undo_index = getattr(self.plugin, "_vial_undo_index", None)
        was_editable = getattr(self.plugin, "_vial_was_editable", False)

        try:
            if layer.isEditable():
                if undo_index is not None and layer.undoStack():
                    # Si tenemos checkpoint: deshacer solo lo que hizo VIAL
                    st = layer.undoStack()
                    while st.index() > undo_index:
                        st.undo()
                elif not was_editable:
                    # Fallback: si la capa NO era editable antes, hacer rollBack
                    layer.rollBack()

            layer.updateFields()
            layer.triggerRepaint()
            if self.iface:
                self.iface.mapCanvas().refresh()

        except Exception as e:
            QgsMessageLog.logMessage(
                f"[VIAL] Error revertiendo cambios al volver al mapeo: {e}",
                "VIAL",
                Qgis.Warning
            )

        self.close()
        self.plugin.run_attr_editor_flow()


    # ---------- Modo de vista (combo Show all / selected / visible) ----------

    def _on_view_mode_changed(self, idx: int):
        from qgis.core import QgsMessageLog, Qgis
        
        mode = self.view_mode_combo.itemData(idx)
        if mode is None:
            return
        
        QgsMessageLog.logMessage(
            f"[DEBUG] _on_view_mode_changed: idx={idx}, mode={mode}",
            "VIAL",
            Qgis.Info
        )
        
        QgsMessageLog.logMessage(
            f"[DEBUG] Filas antes de cambiar modo: {self.proxy.rowCount()}",
            "VIAL",
            Qgis.Info
        )
        
        self.proxy.setFilterMode(mode)
        
        QgsMessageLog.logMessage(
            f"[DEBUG] Filas después de cambiar modo: {self.proxy.rowCount()}",
            "VIAL",
            Qgis.Info
        )
        
        # Comprobar si btn_sel_top está activado
        QgsMessageLog.logMessage(
            f"[DEBUG] btn_sel_top.isChecked(): {self.btn_sel_top.isChecked()}",
            "VIAL",
            Qgis.Info
        )

    # ---------- Sugerencias (por ahora mensajes informativos) ----------

    def _on_apply_selected_suggestions(self):
        """
        Aplica el nombre sugerido SOLO para las filas seleccionadas en la tabla
        de sugerencias. No aplica sugerencias en conflicto.
        """
        # --- Si estamos en revisión de vía generadora, aplicar checkboxes del details_table ---
        if self._details_mode == "vg_review":
            self._apply_vg_review_marked()
            return
 
        if getattr(self, "_global_conflicts_mode", False):
            self.iface.messageBar().pushMessage(
                "VIAL",
                "Accion no disponible en revision de conflictos globales.",
                Qgis.Info,
                4,
            )
            return

        if not self._contiguous_suggestions:
            return

        sel_model = self.sugg_table.selectionModel()
        if not sel_model or not sel_model.hasSelection():
            self.iface.messageBar().pushMessage(
                "VIAL",
                "No hay sugerencias seleccionadas.",
                Qgis.Info,
                3,
            )
            return

        rows = sorted({idx.row() for idx in sel_model.selectedRows()})
        self._apply_suggestions_for_rows(rows)
        
        # Limpiar sugerencias y cerrar panel de detalles
        self._contiguous_suggestions = []
        self._reset_suggestions_model()
        self.details_group.setVisible(False)
        self._set_suggestions_action_state(is_global_conflicts=False)

    def _on_apply_all_suggestions(self):
        """
        Aplica TODAS las sugerencias que no están en conflicto.
        """
        # --- Si estamos en revisión de vía generadora, aplicar TODAS (incluye las no marcadas) ---
        if self._details_mode == "vg_review":
            self._apply_vg_review_all()
            return     
           
        if getattr(self, "_global_conflicts_mode", False):
            self.iface.messageBar().pushMessage(
                "VIAL",
                "Accion no disponible en revision de conflictos globales.",
                Qgis.Info,
                4,
            )
            return

        if not self._contiguous_suggestions:
            return

        rows = [
            i for i, s in enumerate(self._contiguous_suggestions)
            if not s.get("has_conflict", False)
        ]
        if not rows:
            self.iface.messageBar().pushMessage(
                "VIAL",
                "No hay sugerencias sin conflicto para aplicar.",
                Qgis.Info,
                3,
            )
            return

        self._apply_suggestions_for_rows(rows)
        
        # Limpiar sugerencias y cerrar panel de detalles
        self._contiguous_suggestions = []
        self._reset_suggestions_model()
        self.details_group.setVisible(False)
        self._set_suggestions_action_state(is_global_conflicts=False)

    def _apply_suggestions_for_rows(self, rows):
        """
        Aplica en la capa las sugerencias indicadas por índice de fila
        para los campos VIAL (tipo_via, nombre_via, numero_via,
        letra_principal, prefijo_principal, letra_prefijo_principal,
        acto_admin, nombre_popular).

        No aplica sugerencias de cadenas marcadas con conflicto.
        Además respeta las exclusiones definidas en self._chain_exclusions:
        para cada chain_id puede haber un set de FIDs en los que NO se
        deben aplicar cambios aunque la sugerencia exista.

        ⚠️ Importante:
        - Se hace snapshot y se actualizan 'historico_nom' y 'fecha_cambio'
        para TODOS los tramos de las cadenas afectadas (no solo los que cambian).
        - Los cambios automáticos del plugin NO deben marcarse como "dirty"
        ni reactivar lógica automática: para eso usamos _suppress_attr_changed
        (NO blockSignals).
        """
        if not self.layer:
            return

        if not self.layer.isEditable():
            if not self.layer.startEditing():
                self.iface.messageBar().pushMessage(
                    "VIAL",
                    "No se pudo iniciar la edición de la capa para aplicar las sugerencias.",
                    Qgis.Critical,
                    5,
                )
                return

        changes = 0

        # Guardaremos el estado "antes del cambio" sólo una vez por FID
        # clave: fid -> dict con valores VIAL actuales (antes de modificar)
        before_snapshots = {}

        # Timestamp global de esta operación, en UTC (independiente del país)
        timestamp_utc = QDateTime.currentDateTimeUtc()
        timestamp_str = timestamp_utc.toString(Qt.ISODate)

        # Configuración de campos VIAL (para snapshots de histórico)
        vial_fields_cfg = get_vial_snapshot_fields_cfg(self.layer)

        def _ensure_snapshot_for_fid(fid: int):
            """
            Si todavía no hemos guardado el estado 'antes' de este FID,
            lo leemos de la capa y almacenamos los campos VIAL.
            """
            if fid in before_snapshots:
                return

            feat = self.layer.getFeature(fid)
            if not feat.isValid():
                return

            snap = {}
            for fname, fidx in vial_fields_cfg:
                if fidx < 0:
                    continue
                val = feat[fidx]
                if val is None:
                    snap[fname] = None
                else:
                    text = str(val).strip()
                    if text.upper() == "NULL":
                        text = ""
                    snap[fname] = text or None

            before_snapshots[fid] = snap

        # ------------------------------------------------------------------
        # 1) PRE-PASO: decidir para qué FIDs vamos a guardar snapshot/fecha
        # ------------------------------------------------------------------
        fids_to_stamp = set()

        for row in rows:
            if row < 0 or row >= len(self._contiguous_suggestions):
                continue

            s = self._contiguous_suggestions[row]
            if s.get("has_conflict", False):
                continue

            chain_id = s.get("chain_id")
            excluded = self._chain_exclusions.get(chain_id, set())
            chain_fids = s.get("chain_fids", []) or []

            for fid in chain_fids:
                if fid in excluded:
                    continue
                fids_to_stamp.add(fid)

        # Guardar snapshot ANTES de modificar nada
        if (self._idx_historico_nom >= 0 or self._idx_fecha_cambio >= 0) and fids_to_stamp:
            for fid in fids_to_stamp:
                _ensure_snapshot_for_fid(fid)

        # ------------------------------------------------------------------
        # 2) PASO PRINCIPAL: aplicar sugerencias
        # 3) POST-PASO: escribir historico_nom y fecha_cambio
        #
        # ✅ NO bloqueamos señales del layer, para que la tabla se refresque.
        # ✅ Suprimimos nuestra lógica (_on_layer_attribute_changed) con un flag.
        # ------------------------------------------------------------------
        self.layer.beginEditCommand("VIAL: aplicar sugerencias")
        self._suppress_attr_changed = True
        try:
            # 2) Aplicar cambios solo a los FIDs de change_fids
            for row in rows:
                if row < 0 or row >= len(self._contiguous_suggestions):
                    continue

                s = self._contiguous_suggestions[row]
                if s.get("has_conflict", False):
                    continue

                chain_id = s.get("chain_id")
                excluded = self._chain_exclusions.get(chain_id, set())
                field_results = s.get("fields", {})

                for field_name, fr in field_results.items():
                    if fr.get("has_conflict"):
                        continue

                    suggested = fr.get("suggested")
                    # ✅ permitir string vacío como sugerencia válida (borrado)
                    if suggested is None:
                        continue

                    # si suggested == "" => escribir NULL/None para limpiar el campo
                    new_val = None if (isinstance(suggested, str) and suggested.strip() == "") else suggested

                    # Localizar índice del campo en la capa
                    if field_name == "tipo_via":
                        idx_field = self._idx_tipo_via
                    elif field_name == "nombre_via":
                        idx_field = self._idx_nombre_via
                    elif field_name == "numero_via":
                        idx_field = self._idx_numero_via
                    elif field_name == "letra_principal":
                        idx_field = self._idx_letra_principal
                    elif field_name == "prefijo_principal":
                        idx_field = self._idx_prefijo_principal
                    elif field_name == "cuadrante_principal":
                        idx_field = self._idx_cuadrante_principal
                    elif field_name == "letra_prefijo_principal":
                        idx_field = self._idx_letra_prefijo_principal
                    elif field_name == "acto_admin":
                        idx_field = self._idx_acto_admin
                    elif field_name == "nombre_popular":
                        idx_field = self._idx_nombre_popular
                    else:
                        continue

                    if idx_field < 0:
                        continue

                    for fid in fr.get("change_fids", []):
                        if fid in excluded:
                            continue

                        self.layer.changeAttributeValue(fid, idx_field, new_val)
                        changes += 1

            # 3) Escribir historico_nom y fecha_cambio para todos los FIDs con snapshot
            if before_snapshots and (self._idx_historico_nom >= 0 or self._idx_fecha_cambio >= 0):
                for fid, snap in before_snapshots.items():
                    if self._idx_historico_nom >= 0:
                        payload = {
                            "Campos antes del último cambio": {
                                k: ("" if v is None else str(v))
                                for k, v in snap.items()
                            },
                        }
                        hist_str = json.dumps(payload, ensure_ascii=False)
                        self.layer.changeAttributeValue(fid, self._idx_historico_nom, hist_str)

                    if self._idx_fecha_cambio >= 0:
                        self.layer.changeAttributeValue(fid, self._idx_fecha_cambio, timestamp_str)

        finally:
            self._suppress_attr_changed = False
            self.layer.endEditCommand()

        # ✅ No recargues el modelo: la tabla se actualiza sola por señales
        self.layer.triggerRepaint()

        self.iface.messageBar().pushMessage(
            "VIAL",
            f"Sugerencias aplicadas. Se actualizaron {changes} segmento(s).",
            Qgis.Success if changes > 0 else Qgis.Info,
            4,
        )

    def _apply_vg_review_marked(self):
        """
        Aplica en la capa los cambios de vía generadora SOLO para los casos TV/DG
        que están marcados (checkbox). Usa _vg_review_exclusions para respetar
        las desmarcadas. Escribe historico_nom/fecha_cambio si existen.
        """
        if not self.layer:
            return

        if not self._vg_review_items:
            self.iface.messageBar().pushMessage(
                "VIAL", "No hay casos de vía generadora (TV/DG) para aplicar.", Qgis.Info, 3
            )
            return

        # Determinar base_fids a aplicar
        to_apply = []
        for it in self._vg_review_items:
            base_fid = it.get("base_fid")
            if base_fid is None:
                continue
            if base_fid in self._vg_review_exclusions:
                continue
            to_apply.append(it)

        if not to_apply:
            self.iface.messageBar().pushMessage(
                "VIAL", "No hay casos marcados para aplicar.", Qgis.Info, 3
            )
            return

        # Asegurar edición
        if not self.layer.isEditable():
            if not self.layer.startEditing():
                self.iface.messageBar().pushMessage(
                    "VIAL", "No se pudo iniciar edición para aplicar vía generadora.", Qgis.Critical, 5
                )
                return

        # Histórico/fecha (igual patrón)
        track_history = (self._idx_historico_nom >= 0) or (self._idx_fecha_cambio >= 0)
        before_snapshots = {}
        timestamp_str = None

        if track_history:
            timestamp_utc = QDateTime.currentDateTimeUtc()
            timestamp_str = timestamp_utc.toString(Qt.ISODate)
            vial_fields_cfg = get_vial_snapshot_fields_cfg(self.layer)

            # snapshot solo de base_fids afectados
            for it in to_apply:
                fid = it["base_fid"]
                feat = self.layer.getFeature(fid)
                if not feat.isValid():
                    continue
                snap = {}
                for fname, fidx in vial_fields_cfg:
                    if fidx < 0:
                        continue
                    val = feat[fidx]
                    if val is None:
                        snap[fname] = None
                    else:
                        text = str(val).strip()
                        if text.upper() == "NULL":
                            text = ""
                        snap[fname] = text or None
                before_snapshots[fid] = snap

        # Índices de campos destino
        idx_num = self._idx_num_generadora
        idx_let = self._idx_letra_generadora
        idx_cua = self._idx_cuadrante_generadora
        idx_suf = self._idx_sufijo_generadora
        idx_suflet = self._idx_letra_sufijo_generadora


        if idx_num < 0 or idx_cua < 0:
            self.iface.messageBar().pushMessage(
                "VIAL",
                "Faltan campos requeridos (num_generadora/cuadrante_generadora).",
                Qgis.Critical,
                5,
            )
            return

        changes = 0
        changed_fids = set()

        self.layer.beginEditCommand("VIAL: aplicar via generadora (TV/DG)")
        self._suppress_attr_changed = True
        try:
            for it in to_apply:
                fid = it["base_fid"]
                out_num = it.get("out_num")
                out_letra = it.get("out_letra")
                out_cuad = it.get("out_cuad")
                out_suf = it.get("out_suf")
                out_suf_letra = it.get("out_suf_letra")

                # Copiar tipo_via de la vía generadora sugerida
                if self._idx_tipo_via_generadora >= 0:
                    tipo_value = it.get("gen_tipo", "")
                    tipo_value = str(tipo_value).strip().upper() if tipo_value is not None else ""

                    if tipo_value == "" or tipo_value == "NULL":
                        tipo_value = None

                    self.layer.changeAttributeValue(fid, self._idx_tipo_via_generadora, tipo_value)

                # num_generadora
                self.layer.changeAttributeValue(fid, idx_num, out_num if out_num is not None else None)

                # letra_generadora (si existe)
                if idx_let >= 0:
                    letra_value = None
                    if out_letra and str(out_letra).strip().upper() != "NULL":
                        letra_value = str(out_letra).strip().upper()
                    self.layer.changeAttributeValue(fid, idx_let, letra_value)

                # cuadrante_generadora
                cuad_value = None
                if out_cuad and str(out_cuad).strip().upper() != "NULL":
                    cuad_value = str(out_cuad).strip().upper()
                self.layer.changeAttributeValue(fid, idx_cua, cuad_value)
                
                # sufijo_generadora (BIS)
                if idx_suf >= 0:
                    suf_value = None
                    if out_suf and str(out_suf).strip().upper() != "NULL":
                        suf_value = str(out_suf).strip().upper()
                    self.layer.changeAttributeValue(fid, idx_suf, suf_value)

                # letra_sufijo_generadora
                if idx_suflet >= 0:
                    suflet_value = None
                    if out_suf_letra and str(out_suf_letra).strip().upper() != "NULL":
                        suflet_value = str(out_suf_letra).strip().upper()
                    self.layer.changeAttributeValue(fid, idx_suflet, suflet_value)


                changes += 1
                changed_fids.add(fid)

            # escribir historico/fecha
            if track_history and changed_fids:
                for fid in changed_fids:
                    snap = before_snapshots.get(fid, {})
                    if self._idx_historico_nom >= 0:
                        payload = {
                            "Campos antes del último cambio": {
                                k: ("" if v is None else str(v)) for k, v in snap.items()
                            },
                        }
                        self.layer.changeAttributeValue(
                            fid, self._idx_historico_nom, json.dumps(payload, ensure_ascii=False)
                        )
                    if self._idx_fecha_cambio >= 0 and timestamp_str is not None:
                        self.layer.changeAttributeValue(fid, self._idx_fecha_cambio, timestamp_str)

        finally:
            self._suppress_attr_changed = False
            self.layer.endEditCommand()

        self.layer.triggerRepaint()

        # Limpiar estado de revisión y refrescar panel
        self._vg_review_items = []
        self._vg_review_exclusions = set()
        self.details_group.setVisible(False)
        self._details_mode = None

        self.iface.messageBar().pushMessage(
            "VIAL",
            f"Vía generadora (TV/DG) aplicada. Se actualizaron {changes} segmento(s).",
            Qgis.Success if changes > 0 else Qgis.Info,
            4,
        )

    def _apply_vg_review_all(self):
        """
        Aplica en la capa los cambios de vía generadora para TODOS los casos TV/DG
        (sin respetar checkboxes). Útil para el botón 'Aceptar todas'.
        """
        if not self.layer:
            return

        if not self._vg_review_items:
            self.iface.messageBar().pushMessage(
                "VIAL", "No hay casos de vía generadora (TV/DG) para aplicar.", Qgis.Info, 3
            )
            return

        # Asegurar edición
        if not self.layer.isEditable():
            if not self.layer.startEditing():
                self.iface.messageBar().pushMessage(
                    "VIAL", "No se pudo iniciar edición para aplicar vía generadora.", Qgis.Critical, 5
                )
                return

        track_history = (self._idx_historico_nom >= 0) or (self._idx_fecha_cambio >= 0)
        before_snapshots = {}
        timestamp_str = None

        if track_history:
            timestamp_utc = QDateTime.currentDateTimeUtc()
            timestamp_str = timestamp_utc.toString(Qt.ISODate)
            vial_fields_cfg = get_vial_snapshot_fields_cfg(self.layer)

            for it in self._vg_review_items:
                fid = it.get("base_fid")
                if fid is None:
                    continue
                feat = self.layer.getFeature(fid)
                if not feat.isValid():
                    continue

                snap = {}
                for fname, fidx in vial_fields_cfg:
                    if fidx < 0:
                        continue
                    val = feat[fidx]
                    if val is None:
                        snap[fname] = None
                    else:
                        text = str(val).strip()
                        if text.upper() == "NULL":
                            text = ""
                        snap[fname] = text or None
                before_snapshots[fid] = snap

        idx_num = self._idx_num_generadora
        idx_let = self._idx_letra_generadora
        idx_cua = self._idx_cuadrante_generadora
        idx_suf = self._idx_sufijo_generadora
        idx_suflet = self._idx_letra_sufijo_generadora

        if idx_num < 0 or idx_cua < 0:
            self.iface.messageBar().pushMessage(
                "VIAL",
                "Faltan campos requeridos (num_generadora/cuadrante_generadora).",
                Qgis.Critical,
                5,
            )
            return

        changes = 0
        changed_fids = set()

        self.layer.beginEditCommand("VIAL: aplicar via generadora (TV/DG) - todas")
        self._suppress_attr_changed = True
        try:
            for it in self._vg_review_items:
                fid = it.get("base_fid")
                if fid is None:
                    continue

                out_num = it.get("out_num")
                out_letra = it.get("out_letra")
                out_cuad = it.get("out_cuad")
                out_suf = it.get("out_suf")
                out_suf_letra = it.get("out_suf_letra")

                # Copiar tipo_via de la vía generadora sugerida
                if self._idx_tipo_via_generadora >= 0:
                    tipo_value = it.get("gen_tipo", "")
                    tipo_value = str(tipo_value).strip().upper() if tipo_value is not None else ""

                    if tipo_value == "" or tipo_value == "NULL":
                        tipo_value = None

                    self.layer.changeAttributeValue(fid, self._idx_tipo_via_generadora, tipo_value)

                self.layer.changeAttributeValue(fid, idx_num, out_num if out_num is not None else None)

                if idx_let >= 0:
                    letra_value = None
                    if out_letra and str(out_letra).strip().upper() != "NULL":
                        letra_value = str(out_letra).strip().upper()
                    self.layer.changeAttributeValue(fid, idx_let, letra_value)

                cuad_value = None
                if out_cuad and str(out_cuad).strip().upper() != "NULL":
                    cuad_value = str(out_cuad).strip().upper()
                self.layer.changeAttributeValue(fid, idx_cua, cuad_value)

                # sufijo_generadora (BIS)
                if idx_suf >= 0:
                    suf_value = None
                    if out_suf and str(out_suf).strip().upper() != "NULL":
                        suf_value = str(out_suf).strip().upper()
                    self.layer.changeAttributeValue(fid, idx_suf, suf_value)

                # letra_sufijo_generadora
                if idx_suflet >= 0:
                    suflet_value = None
                    if out_suf_letra and str(out_suf_letra).strip().upper() != "NULL":
                        suflet_value = str(out_suf_letra).strip().upper()
                    self.layer.changeAttributeValue(fid, idx_suflet, suflet_value)

                changes += 1
                changed_fids.add(fid)

            if track_history and changed_fids:
                for fid in changed_fids:
                    snap = before_snapshots.get(fid, {})
                    if self._idx_historico_nom >= 0:
                        payload = {
                            "Campos antes del último cambio": {
                                k: ("" if v is None else str(v)) for k, v in snap.items()
                            },
                        }
                        self.layer.changeAttributeValue(
                            fid, self._idx_historico_nom, json.dumps(payload, ensure_ascii=False)
                        )
                    if self._idx_fecha_cambio >= 0 and timestamp_str is not None:
                        self.layer.changeAttributeValue(fid, self._idx_fecha_cambio, timestamp_str)

        finally:
            self._suppress_attr_changed = False
            self.layer.endEditCommand()

        self.layer.triggerRepaint()

        # Limpiar estado revisión y cerrar panel
        self._vg_review_items = []
        self._vg_review_exclusions = set()
        self.details_group.setVisible(False)
        self._details_mode = None

        self.iface.messageBar().pushMessage(
            "VIAL",
            f"Vía generadora (TV/DG) aplicada (todas). Se actualizaron {changes} segmento(s).",
            Qgis.Success if changes > 0 else Qgis.Info,
            4,
        )

    def _on_clear_suggestions(self):
        self._contiguous_suggestions = []
        self._reset_suggestions_model()
        self._set_suggestions_action_state(is_global_conflicts=False)
        self.iface.messageBar().pushMessage(
            "VIAL",
            "Sugerencias limpiadas.",
            Qgis.Info,
            3,
        )

    # ---------- Sincronización selección capa ↔ tabla ----------

    def _find_row_by_fid_in_filter_model(self, fid):
        """
        Busca la fila en el proxy que corresponde a un FID dado.
        Retorna el número de fila o None si no se encuentra.
        """
        for row in range(self.proxy.rowCount()):
            try:
                src_idx = self.proxy.mapToSource(self.proxy.index(row, 0))
                if not src_idx.isValid():
                    continue
                row_fid = self.model.rowToId(src_idx.row())
                if row_fid == fid:
                    return row
            except Exception:
                continue
        return None

    def _on_layer_selection_changed(self, selected, deselected, clearAndSelect):
        """
        Cuando se selecciona algo en el canvas, resalta solo la celda ID (columna 0)
        en la tabla, sin interferir con otras celdas seleccionadas.
        """
        from qgis.core import QgsMessageLog, Qgis
        
        if getattr(self, '_syncing_from_table', False):
            return
        
        # Respetar el flag de bloqueo cuando se hace clic en la tabla
        if getattr(self, '_block_layer_selection_slot', False):
            return

        self._syncing_from_layer = True
        try:
            fids = list(self.layer.selectedFeatureIds())
            
            QgsMessageLog.logMessage(
                f"[DEBUG] _on_layer_selection_changed: FIDs seleccionados={fids}",
                "VIAL",
                Qgis.Info
            )
            
            QgsMessageLog.logMessage(
                f"[DEBUG] Filas en tabla: {self.proxy.rowCount()}, Modo filtro: {self.proxy.filterMode()}",
                "VIAL",
                Qgis.Info
            )

            sel_model = self.table.selectionModel()
            if not sel_model:
                return

            # Limpiar selección actual (sin disparar señales)
            sel_model.blockSignals(True)
            sel_model.clearSelection()
            sel_model.blockSignals(False)

            # Seleccionar SOLO la columna 0 (ID) para cada FID seleccionado
            item_selection = QItemSelection()
            rows_found = []
            for fid in fids:
                row = self._find_row_by_fid_in_filter_model(fid)
                if row is not None:
                    rows_found.append(row)
                    # Solo seleccionar la celda de la columna 0 (ID)
                    id_cell = self.proxy.index(row, 0)
                    item_selection.select(id_cell, id_cell)
                else:
                    QgsMessageLog.logMessage(
                        f"[DEBUG] FID {fid} no encontrado en proxy (rowCount={self.proxy.rowCount()})",
                        "VIAL",
                        Qgis.Warning
                    )
            
            QgsMessageLog.logMessage(
                f"[DEBUG] Filas encontradas: {rows_found}",
                "VIAL",
                Qgis.Info
            )

            if not item_selection.isEmpty():
                QgsMessageLog.logMessage(
                    f"[DEBUG] Seleccionando {len(item_selection.indexes())} celdas",
                    "VIAL",
                    Qgis.Info
                )
                sel_model.select(item_selection, QItemSelectionModel.Select)
                # Hacer scroll a la primera celda seleccionada
                first_idx = item_selection.indexes()[0]
                self.table.scrollTo(first_idx)
            else:
                QgsMessageLog.logMessage(
                    f"[DEBUG] item_selection está vacío",
                    "VIAL",
                    Qgis.Warning
                )            # Actualizar estado de los botones de selección
            self._update_selection_buttons()

        finally:
            self._syncing_from_layer = False

    def _on_table_selection_changed(self, selected, deselected):
        if self._syncing_from_layer:
            return

        self._syncing_from_table = True
        try:
            rows = self.table.selectionModel().selectedRows()
            new_fids = set()

            # Obtener los FIDs correspondientes a las filas seleccionadas
            for idx in rows:
                try:
                    fid = int(self.proxy.rowToId(idx))
                    new_fids.add(fid)
                except Exception:
                    pass

            # Seleccionar los segmentos correspondientes en el mapa
            self.layer.selectByIds(list(new_fids))

        finally:
            self._syncing_from_table = False


    def closeEvent(self, event):
        """
        Maneja el cierre del dock. Si hay cambios sin guardar, pregunta al usuario
        si desea guardarlos, descartarlos o cancelar el cierre.
        """
        # Desconectar señales
        try:
            self.layer.selectionChanged.disconnect(self._on_layer_selection_changed)
        except Exception:
            pass
        try:
            self.layer.attributeValueChanged.disconnect(self._on_layer_attribute_changed)
        except Exception:
            pass

        # Verificar si hay cambios sin guardar
        if self.layer and self.layer.isEditable() and self.layer.isModified():
            reply = QMessageBox.question(
                self,
                "VIAL — Cambios sin guardar",
                "Hay cambios sin guardar en la capa.\n\n¿Qué deseas hacer?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Cancel
            )

            if reply == QMessageBox.Save:
                # Guardar cambios
                if not self.layer.commitChanges():
                    QMessageBox.critical(
                        self,
                        "Error",
                        f"No se pudieron guardar los cambios:\n{self.layer.commitErrors()}"
                    )
                    event.ignore()
                    return
            elif reply == QMessageBox.Discard:
                # Descartar cambios
                self.layer.rollBack()
            else:  # Cancel
                # Cancelar cierre
                event.ignore()
                return

        super().closeEvent(event)


    # ---------- Selected to top / vía generadora ----------

    def _on_selected_to_top_toggled(self, checked: bool):
        """
        Usa la lógica de QgsAttributeTableFilterModel para poner
        los seleccionados en la parte superior.
        
        IMPORTANTE: setSelectedOnTop() tiene un side-effect que muestra
        todas las columnas. Después de activarlo, debemos volver a ocultar
        las columnas que no pertenecen a REQUIRED_FIELDS.
        """
        self.proxy.setSelectedOnTop(checked)
        # Volver a configurar las columnas visibles después de setSelectedOnTop()
        # porque este método tiene el side-effect de mostrar todas las columnas
        self._configure_visible_columns()
        self._apply_canonical_column_order()

    def _line_main_direction_deg(self, geom):
        """
        Devuelve un ángulo en grados (0–180) que representa la dirección
        principal de una geometría de línea, o None si no se puede calcular.

        0° y 180° se consideran misma calle (ida/vuelta).
        """
        if geom is None or geom.isEmpty():
            return None

        pts = geom.asPolyline()
        if not pts:
            multi = geom.asMultiPolyline()
            if not multi:
                return None
            pts = multi[0]

        if len(pts) < 2:
            return None

        p0 = pts[0]
        p1 = pts[-1]
        dx = p1.x() - p0.x()
        dy = p1.y() - p0.y()
        if dx == 0 and dy == 0:
            return None

        ang = math.degrees(math.atan2(dy, dx))  # -180..180
        # ignoramos el sentido: 0° y 180° son la misma dirección
        if ang < 0:
            ang += 180.0
        return ang


    def _on_calc_via_generadora_clicked(self):
        """
        Calcula la 'vía generadora' para TODOS los segmentos de la capa.

        Para cada segmento:
        - Busca otros segmentos que se crucen con él (intersección geométrica).
        - Descarta los que tienen una dirección muy parecida (misma calle).
        - Entre los candidatos restantes escoge el de menor 'numero_via'.
        - Escribe ese número en 'num_generadora' y, si existe,
        su 'letra_principal' en 'letra_generadora'.

        El botón solo se habilita cuando todos los segmentos tienen 'numero_via'
        numérico y no nulo.
        """
        if not self.layer:
            return

        if self._idx_numero_via < 0:
            self.iface.messageBar().pushMessage(
                "VIAL",
                "La capa no tiene el campo 'numero_via'; no se puede calcular la vía generadora.",
                Qgis.Critical,
                5,
            )
            return

        if self._idx_num_generadora < 0:
            self.iface.messageBar().pushMessage(
                "VIAL",
                "La capa no tiene el campo 'num_generadora'; crea este campo antes de ejecutar la acción.",
                Qgis.Critical,
                5,
            )
            return

        if self._idx_cuadrante_generadora < 0:
            self.iface.messageBar().pushMessage(
                "VIAL",
                "La capa no tiene el campo 'cuadrante_generadora'; crea este campo antes de ejecutar la acción.",
                Qgis.Critical,
                5,
            )
            return
        
        if self._idx_sufijo_generadora < 0:
            self.iface.messageBar().pushMessage(
                "VIAL",
                "La capa no tiene el campo 'sufijo_generadora'; crea este campo antes de ejecutar la acción.",
                Qgis.Critical,
                5,
            )
            return

        if self._idx_letra_sufijo_generadora < 0:
            self.iface.messageBar().pushMessage(
                "VIAL",
                "La capa no tiene el campo 'letra_sufijo_generadora'; crea este campo antes de ejecutar la acción.",
                Qgis.Critical,
                5,
            )
            return

        has_letra_principal = self._idx_letra_principal >= 0
        has_letra_generadora = self._idx_letra_generadora >= 0

        # Revalidar condición de entrada
        self._update_calc_via_generadora_state()
        if not self.btn_calc_via_generadora.isEnabled():
            self.iface.messageBar().pushMessage(
                "VIAL",
                "No todos los segmentos tienen 'numero_via' numérico; revisa la capa antes de continuar.",
                Qgis.Warning,
                5,
            )
            return

        # Asegurar modo edición
        if not self.layer.isEditable():
            if not self.layer.startEditing():
                self.iface.messageBar().pushMessage(
                    "VIAL",
                    "No se pudo iniciar la edición de la capa para calcular la vía generadora.",
                    Qgis.Critical,
                    5,
                )
                return

        # Construir lista de features
        all_feats = list(self.layer.getFeatures())
        if not all_feats:
            self.iface.messageBar().pushMessage(
                "VIAL",
                "La capa no tiene entidades.",
                Qgis.Info,
                3,
            )
            return

        # ----------------------------------------------
        # Preparar histórico/fecha antes de modificar nada
        # ----------------------------------------------
        track_history = (self._idx_historico_nom >= 0) or (self._idx_fecha_cambio >= 0)
        before_snapshots = {}
        timestamp_str = None

        if track_history:
            # Timestamp global de esta operación
            timestamp_utc = QDateTime.currentDateTimeUtc()
            timestamp_str = timestamp_utc.toString(Qt.ISODate)

            vial_fields_cfg = get_vial_snapshot_fields_cfg(self.layer)

            for f in all_feats:
                fid = f.id()
                snap = {}
                for fname, fidx in vial_fields_cfg:
                    if fidx < 0:
                        continue
                    val = f[fidx]
                    if val is None:
                        snap[fname] = None
                    else:
                        text = str(val).strip()
                        if text.upper() == "NULL":
                            text = ""
                        snap[fname] = text or None
                before_snapshots[fid] = snap

        # Crear índice espacial y añadir las entidades
        spatial_index = QgsSpatialIndex()
        spatial_index.addFeatures(all_feats)

        # Pre-calcular información por FID
        info_by_fid = {}
        idx_cuad_principal = self._idx_cuadrante_principal
        for f in all_feats:
            fid = f.id()
            geom = f.geometry()
            ang = self._line_main_direction_deg(geom)

            # numero_via (obligatorio para la lógica de generadora)
            num_txt = str(f[self._idx_numero_via]).strip()
            try:
                num_via = int(num_txt)
            except Exception:
                num_via = None

            # letra_principal
            letra = ""
            if has_letra_principal:
                val = f[self._idx_letra_principal]
                if val is not None:
                    letra = str(val).strip().upper()
                    if letra == "NULL":
                        letra = ""

            cuad = ""
            if idx_cuad_principal >= 0:
                v = f[idx_cuad_principal]
                if v is not None:
                    cuad = str(v).strip().upper()
                    if cuad == "NULL":
                        cuad = ""

            tipo = ""
            if self._idx_tipo_via >= 0:
                v = f[self._idx_tipo_via]
                if v is not None:
                    tipo = str(v).strip().upper()
                    if tipo == "NULL":
                        tipo = ""
            
            # prefijo_principal (BIS) y letra_prefijo_principal
            pref = ""
            if self._idx_prefijo_principal >= 0:
                v = f[self._idx_prefijo_principal]
                if v is not None:
                    pref = str(v).strip().upper()
                    if pref == "NULL":
                        pref = ""

            pref_letra = ""
            if self._idx_letra_prefijo_principal >= 0:
                v = f[self._idx_letra_prefijo_principal]
                if v is not None:
                    pref_letra = str(v).strip().upper()
                    if pref_letra == "NULL":
                        pref_letra = ""


            info_by_fid[fid] = {
                "geom": geom,
                "angle": ang,
                "numero_via": num_via,
                "letra": letra,
                "cuadrante": cuad,
                "tipo_via": tipo,
                "prefijo": pref,
                "letra_prefijo": pref_letra,
            }

        angle_thresh = 15.0  # grados; si la diferencia es menor, consideramos misma calle

        changes = 0
        changed_fids = set()

        QgsApplication.setOverrideCursor(Qt.WaitCursor)
        # Preparar revisión TV/DG
        self._vg_review_items = []
        self._vg_review_exclusions = set()

        try:
            for f in all_feats:
                fid = f.id()
                base_info = info_by_fid[fid]
                geom = base_info["geom"]
                base_angle = base_info["angle"]

                if geom is None or geom.isEmpty():
                    continue

                base_tipo = (base_info.get("tipo_via") or "").strip().upper()


                # Candidatos: entidades cuyo bbox intersecta
                candidate_ids = spatial_index.intersects(geom.boundingBox())

                best_key = None
                best_fid = None
                best_num = None
                best_letra = ""
                best_cuadrante = ""

                # --- tracking para regla especial (min numero_via y sus candidatos) ---
                min_num = None
                min_num_fids = []          # FIDs de candidatos con numero_via == min_num
                min_num_quads = []         # cuadrantes (normalizados) correspondientes

                # ✅ override debe existir aunque no haya candidatos
                override_num = None
                override_letra = ""   # se limpia (se deja por claridad)
                override_cuad = ""

                # ------------------------------------------------------------
                # 1) LOOP de candidatos: SOLO evaluar (ranking + tracking)
                # ------------------------------------------------------------
                for other_id in candidate_ids:
                    if other_id == fid:
                        continue

                    other_info = info_by_fid[other_id]
                    other_geom = other_info["geom"]

                    if other_geom is None or other_geom.isEmpty():
                        continue

                    # Deben tener intersección geométrica real
                    if not geom.intersects(other_geom):
                        continue

                    # ------------------------------------------------------------
                    # EXCLUSIÓN ESTRUCTURAL (mínimo impacto):
                    # - TV nunca puede ser generadora de KR
                    # - DG nunca puede ser generadora de CL
                    # Se excluye ANTES del ranking y NO se manda a revisión.
                    # ------------------------------------------------------------
                    other_tipo = (other_info.get("tipo_via") or "").strip().upper()

                    if base_tipo == "KR" and other_tipo == "TV":
                        continue  # TV no compite contra KR

                    if base_tipo == "CL" and other_tipo == "DG":
                        continue  # DG no compite contra CL

                    # Evitar la misma calle: dirección muy similar
                    other_angle = other_info["angle"]
                    if base_angle is not None and other_angle is not None:
                        diff = abs(base_angle - other_angle)
                        diff = min(diff, 180.0 - diff)
                        if diff < angle_thresh:
                            # Casi paralelos → misma calle → no es generadora
                            continue

                    other_num = other_info["numero_via"]
                    if other_num is None:
                        continue

                    # Trackear candidatos con el menor numero_via observado
                    other_quad = (other_info.get("cuadrante") or "").strip().upper()
                    if other_quad == "NULL":
                        other_quad = ""

                    if min_num is None or other_num < min_num:
                        min_num = other_num
                        min_num_fids = [other_id]
                        min_num_quads = [other_quad]
                    elif other_num == min_num:
                        min_num_fids.append(other_id)
                        min_num_quads.append(other_quad)

                    other_letra = (other_info.get("letra") or "").strip().upper()
                    if other_letra == "NULL":
                        other_letra = ""

                    # Ranking:
                    # 1) menor numero_via
                    # 2) sin letra gana (0) vs con letra (1)
                    # 3) si ambos con letra: A < B < ... < Z
                    # 4) (desempate) preferir cuadrante explícito sobre vacío ("")
                    quad_rank = 0 if other_quad else 1
                    key = (other_num, 1 if other_letra else 0, other_letra, quad_rank)


                    if best_key is None or key < best_key:
                        best_key = key
                        best_fid = other_id
                        best_num = other_num
                        best_letra = other_letra
                        best_cuadrante = other_quad

                # ------------------------------------------------------------
                # 2) Regla especial: empate entre candidatos con numero_via = 1
                # y cuadrantes opuestos:
                #   - si existe al menos un 1N y al menos un 1S => generadora = 0S
                #   - si existe al menos un 1E y al menos un 1O => generadora = 0E
                #
                # Nota: NO exigimos exactamente 2 candidatos, porque puede haber
                # duplicados/fragmentos adicionales con numero_via=1 que también
                # intersecten al segmento base.
                # ------------------------------------------------------------
                if min_num == 1:
                    # ------------------------------------------------------------
                    # NUEVO: si entre los candidatos con numero_via==1 hay alguna letra,
                    # NO aplicar regla especial (ni implícito por vacío, ni override 0S/0E).
                    # Se resuelve con el ranking normal.
                    # ------------------------------------------------------------
                    has_letter_among_ones = False
                    for cand_fid in min_num_fids:
                        cand_letra = (info_by_fid.get(cand_fid, {}).get("letra") or "").strip().upper()
                        if cand_letra == "NULL":
                            cand_letra = ""
                        if cand_letra:
                            has_letter_among_ones = True
                            break

                    if not has_letter_among_ones:
                        # Cuadrantes presentes (incluye vacío)
                        q_all = [(q or "").strip().upper() for q in min_num_quads]
                        q_all = [("" if q == "NULL" else q) for q in q_all]

                        has_empty = any(q == "" for q in q_all)

                        hasN = any(q == "N" for q in q_all)
                        hasS = any(q == "S" for q in q_all)
                        hasE = any(q == "E" for q in q_all)
                        hasO = any(q == "W" for q in q_all)

                        # No consideramos mezclas N/S con E/O; si pasa, lo tratamos como error del usuario => no override
                        mixes_ns_eo = (hasN or hasS) and (hasE or hasO)
                        if not mixes_ns_eo:
                            # Caso clásico: existen ambos opuestos explícitos
                            if hasN and hasS:
                                override_num = 0
                                override_cuad = "S"
                            elif hasE and hasO:
                                override_num = 0
                                override_cuad = "E"
                            else:
                                # Extensión: tratar 1(sin cuadrante) como implícito según contexto
                                # - 1S vs 1(vacío) => como 1S vs 1N => 0S
                                if hasS and has_empty:
                                    override_num = 0
                                    override_cuad = "S"
                                # - 1O vs 1(vacío) => como 1O vs 1E => 0E
                                elif hasO and has_empty:
                                    override_num = 0
                                    override_cuad = "E"
                                # Caso específico: 1N y 1(vacío) => queda 1N (no override)
                                # Caso específico: 1E y 1(vacío) => queda 1E (no override)
                                # (No hacemos nada aquí; el ranking + desempate de cuadrante explícito resuelve.)
                # ------------------------------------------------------------
                # 3) Decidir salida final (override > ranking)
                # ------------------------------------------------------------
                out_num = None
                out_letra = ""
                out_cuad = ""
                out_suf = ""
                out_suf_letra = ""

                if override_num is not None:
                    out_num = override_num
                    out_letra = ""
                    out_cuad = override_cuad
                    out_suf = ""
                    out_suf_letra = ""

                elif best_fid is not None and best_num is not None:
                    best_info = info_by_fid.get(best_fid, {})
                    out_num = best_info.get("numero_via", best_num)
                    out_letra = best_info.get("letra", best_letra)
                    out_cuad = best_info.get("cuadrante", best_cuadrante)
                    out_suf = ""  # sufijo_generadora siempre NULL
                    out_suf_letra = ""  # letra_sufijo_generadora siempre NULL

                # ------------------------------------------------------------
                # 4) Caso especial: si la generadora real es TV o DG => NO aplicar
                #    (solo si NO es override y existe best_fid)
                # ------------------------------------------------------------
                is_override = (override_num is not None)

                gen_fid = None
                gen_tipo = ""

                if (not is_override) and (best_fid is not None) and (out_num is not None):
                    gen_fid = best_fid
                    gen_tipo = (info_by_fid.get(gen_fid, {}).get("tipo_via") or "").strip().upper()

                if gen_tipo in ("TV", "DG"):
                    self._vg_review_items.append({
                        "base_fid": fid,
                        "base_num": base_info.get("numero_via"),
                        "base_letra": base_info.get("letra", "") or "",
                        "base_cuad": base_info.get("cuadrante", "") or "",

                        "gen_fid": gen_fid,
                        "gen_tipo": gen_tipo,

                        "out_num": out_num,
                        "out_letra": out_letra,
                        "out_cuad": out_cuad,
                        "out_suf": out_suf,
                        "out_suf_letra": out_suf_letra,
                    })
                    continue  # ⛔ NO escribir en capa para este fid (se revisa en panel)

                # ------------------------------------------------------------
                # 5) Caso normal: aplicar como hoy (incluye override 0S/0E)
                # Copiar: tipo_via, numero_via, letra_principal, cuadrante_principal
                # a: tipo_via_generadora, num_generadora, letra_generadora, cuadrante_generadora
                # ------------------------------------------------------------
                if out_num is not None:
                    # Copiar tipo_via de la vía generadora real a tipo_via_generadora
                    if self._idx_tipo_via_generadora >= 0:
                        tipo_value = None

                        if best_fid is not None:
                            best_info = info_by_fid.get(best_fid, {})
                            tipo_value = (best_info.get("tipo_via") or "").strip().upper()

                            if tipo_value == "NULL" or tipo_value == "":
                                tipo_value = None

                        self.layer.changeAttributeValue(fid, self._idx_tipo_via_generadora, tipo_value)
                    
                    self.layer.changeAttributeValue(fid, self._idx_num_generadora, out_num)

                    if has_letra_generadora:
                        letra_value = None
                        if out_letra and out_letra.upper() != "NULL":
                            letra_value = out_letra
                        self.layer.changeAttributeValue(fid, self._idx_letra_generadora, letra_value)

                    if self._idx_cuadrante_generadora >= 0:
                        cuad_value = None
                        if out_cuad and out_cuad.upper() != "NULL":
                            cuad_value = out_cuad
                        self.layer.changeAttributeValue(fid, self._idx_cuadrante_generadora, cuad_value)
                    # sufijo_generadora (BIS) y letra_sufijo_generadora
                    suf_value = None
                    if out_suf and str(out_suf).strip().upper() != "NULL":
                        suf_value = str(out_suf).strip().upper()
                    self.layer.changeAttributeValue(fid, self._idx_sufijo_generadora, suf_value)

                    suf_letra_value = None
                    if out_suf_letra and str(out_suf_letra).strip().upper() != "NULL":
                        suf_letra_value = str(out_suf_letra).strip().upper()
                    self.layer.changeAttributeValue(fid, self._idx_letra_sufijo_generadora, suf_letra_value)

                    changes += 1
                    changed_fids.add(fid)
                else:
                    # Si no hay via generadora, limpiar campos para evitar valores previos
                    if self._idx_tipo_via_generadora >= 0:
                        self.layer.changeAttributeValue(fid, self._idx_tipo_via_generadora, None)
                    self.layer.changeAttributeValue(fid, self._idx_num_generadora, None)
                    if has_letra_generadora:
                        self.layer.changeAttributeValue(fid, self._idx_letra_generadora, None)
                    if self._idx_cuadrante_generadora >= 0:
                        self.layer.changeAttributeValue(fid, self._idx_cuadrante_generadora, None)
                    self.layer.changeAttributeValue(fid, self._idx_sufijo_generadora, None)
                    self.layer.changeAttributeValue(fid, self._idx_letra_sufijo_generadora, None)

                    changes += 1
                    changed_fids.add(fid)


        finally:
            QgsApplication.restoreOverrideCursor()

        # ----------------------------------------------
        # Escribir historico_nom y fecha_cambio
        # solo para los segmentos que realmente cambiaron
        # ----------------------------------------------
        if track_history and before_snapshots and changed_fids:
            for fid in changed_fids:
                snap = before_snapshots.get(fid, {})
                # 1) Guardar histórico en JSON compacto
                if self._idx_historico_nom >= 0:
                    payload = {
                        "Campos antes del último cambio": {
                            k: ("" if v is None else str(v))
                            for k, v in snap.items()
                        },
                    }
                    hist_str = json.dumps(payload, ensure_ascii=False)
                    self.layer.changeAttributeValue(fid, self._idx_historico_nom, hist_str)

                # 2) Guardar fecha/hora del cambio
                if self._idx_fecha_cambio >= 0 and timestamp_str is not None:
                    self.layer.changeAttributeValue(fid, self._idx_fecha_cambio, timestamp_str)

        # Refrescar modelo y vista
        self.model.loadLayer()
        self.layer.triggerRepaint()

        # Si hay casos TV/DG, abrir panel de revisión
        if self._vg_review_items:
            self._populate_vg_review_table()
            self.iface.messageBar().pushMessage(
                "VIAL",
                f"Vía generadora calculada. Se aplicaron {changes} casos normales y hay {len(self._vg_review_items)} caso(s) TV/DG para revisión (usa 'Aplicar sugerencias seleccionadas').",
                Qgis.Warning,
                6,
            )
            return

        self.iface.messageBar().pushMessage(
            "VIAL",
            f"Cálculo de 'vía generadora' completado. Se actualizaron {changes} segmento(s).",
            Qgis.Success if changes > 0 else Qgis.Info,
            4,
        )
