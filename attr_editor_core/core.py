# -*- coding: utf-8 -*-
import json
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QMessageBox
from qgis.core import (
    QgsProject, QgsField
)
from qgis.PyQt.QtCore import QVariant

from .constants import REQUIRED_FIELDS
from .mapping_dialog import MappingDialog
from .editor_dock import AttrEditorDock


class AttrEditorCore:
    """
    Tool 2 controller (estado + flujo):
    mapping dialog -> crear campos -> transferir -> dock editor
    """
    PROJECT_GROUP = "VIAL"
    PROJECT_KEY_ACTIVE_LAYER = "tool2_active_layer"

    LAYER_PROP_ENABLED = "vial_tool2/enabled"
    LAYER_PROP_MAPPING = "vial_tool2/mapping"

    def __init__(self, iface):
        self.iface = iface
        self._dock = None
        self._layer = None

    # ---------------- PUBLIC ENTRY ----------------
    def start(self):
        """
        Se llama al activar Tool 2.
        - Si hay una sesión real (mapping guardado) => abre dock
        - Si no => abre Mapping Dialog
        """
        lyr = self._restore_layer_from_project()

        if lyr is not None:
            mapping = self._read_mapping(lyr)
            if self._has_saved_state(lyr) and mapping:
                self._layer = lyr
                self._open_dock(lyr)
                return

        self._open_mapping_dialog(
            initial_layer=lyr,
            initial_mapping=self._read_mapping(lyr) if lyr else None
        )

    # ---------------- STATE ----------------
    def _restore_layer_from_project(self):
        prj = QgsProject.instance()

        # readEntry devuelve (value, ok)
        layer_id, ok = prj.readEntry(self.PROJECT_GROUP, self.PROJECT_KEY_ACTIVE_LAYER, "")

        if not ok or not layer_id:
            return None

        lyr = prj.mapLayer(layer_id)
        return lyr

    def _save_active_layer_in_project(self, layer):
        QgsProject.instance().writeEntry(self.PROJECT_GROUP, self.PROJECT_KEY_ACTIVE_LAYER, layer.id())

    def _has_saved_state(self, layer):
        if layer is None:
            return False
        return bool(layer.customProperty(self.LAYER_PROP_ENABLED, False))

    def _read_mapping(self, layer):
        if layer is None:
            return {}
        s = layer.customProperty(self.LAYER_PROP_MAPPING, "")
        try:
            return json.loads(s) if s else {}
        except Exception:
            return {}

    def _save_mapping(self, layer, mapping):
        layer.setCustomProperty(self.LAYER_PROP_MAPPING, json.dumps(mapping, ensure_ascii=False))
        layer.setCustomProperty(self.LAYER_PROP_ENABLED, True)

    # ---------------- FLOW ----------------
    def _open_mapping_dialog(self, initial_layer=None, initial_mapping=None):
        dlg = MappingDialog(
            iface=self.iface,
            initial_layer=initial_layer,
            initial_mapping=initial_mapping,
            parent=self.iface.mainWindow()
        )
        if dlg.exec_() != dlg.Accepted:
            return

        layer, mapping = dlg.get_selected_layer_and_mapping()
        if layer is None:
            return

        self._layer = layer
        self._save_active_layer_in_project(layer)

        # arrancar edición y preparar capa
        self.iface.setActiveLayer(layer)
        self._ensure_editing(layer)
        self._ensure_required_fields(layer)
        self._transfer_values(layer, mapping)

        # guardar mapping
        self._save_mapping(layer, mapping)

        # abrir dock
        self._open_dock(layer)

        # Activar selección para UX (si existe)
        if hasattr(self.iface, "actionSelectRectangle") and self.iface.actionSelectRectangle():
            self.iface.actionSelectRectangle().trigger()
        elif hasattr(self.iface, "actionSelect") and self.iface.actionSelect():
            self.iface.actionSelect().trigger()


    def _open_dock(self, layer):
        # si ya existe, solo traerlo al frente
        if self._dock is not None:
            try:
                self._dock.setVisible(True)
                self._dock.raise_()
                return
            except Exception:
                self._dock = None

        self._dock = AttrEditorDock(self.iface, layer, parent=self.iface.mainWindow())

        self._dock.request_remap.connect(self._on_request_remap)
        self._dock.dock_closed.connect(self._on_dock_closed)

        self.iface.addDockWidget(Qt.RightDockWidgetArea, self._dock)
        self._dock.show()

    def _on_request_remap(self):
        if self._layer is None:
            return
        mapping = self._read_mapping(self._layer)

        # cerramos visualmente el dock (pero dejamos capa en edición)
        if self._dock is not None:
            try:
                self.iface.removeDockWidget(self._dock)
            except Exception:
                pass
            self._dock = None

        self._open_mapping_dialog(initial_layer=self._layer, initial_mapping=mapping)

    def _on_dock_closed(self):
        """
        Si el usuario cierra el dock accidentalmente:
        - NO hacemos commit/rollback
        - NO borramos estado
        Solo removemos referencia para poder restaurar.
        """
        if self._dock is not None:
            try:
                self.iface.removeDockWidget(self._dock)
            except Exception:
                pass
        self._dock = None

    # ---------------- LAYER OPS ----------------
    def _ensure_editing(self, layer):
        if not layer.isEditable():
            layer.startEditing()

    def _ensure_required_fields(self, layer):
        """
        Crea los REQUIRED_FIELDS que falten.
        """
        existing = {f.name() for f in layer.fields()}
        to_add = []
        for spec in REQUIRED_FIELDS:
            if spec["name"] not in existing:
                to_add.append(QgsField(spec["name"], spec["type"]))

        if not to_add:
            return

        for f in to_add:
            layer.addAttribute(f)   # <-- se revierte con "Descartar cambios"
        layer.updateFields()

    def _transfer_values(self, layer, mapping):
        """
        Copia valores desde source -> target.
        Regla segura por defecto: SOLO rellena si target está vacío/NULL.
        """
        # indices
        field_index = {f.name(): i for i, f in enumerate(layer.fields())}

        # target <- source
        transfers = []
        for spec in REQUIRED_FIELDS:
            target = spec["name"]
            source = mapping.get(target)
            if not source:
                continue
            if target not in field_index or source not in field_index:
                continue
            transfers.append((field_index[target], field_index[source]))

        if not transfers:
            return

        for ft in layer.getFeatures():
            fid = ft.id()
            for tgt_idx, src_idx in transfers:
                current = ft.attribute(tgt_idx)
                if current is not None and str(current).strip() != "" and str(current).upper() != "NULL":
                    continue
                val = ft.attribute(src_idx)
                layer.changeAttributeValue(fid, tgt_idx, val)
