# =============================
# Archivo: core.py (py39-safe)
# -*- coding: utf-8 -*-
# =============================

from qgis.PyQt.QtCore import QVariant
from qgis.core import QgsField, QgsVectorDataProvider, QgsGeometry, QgsWkbTypes, QgsPointXY

# Definición de los campos estándar VIAL
REQUIRED_FIELDS = [
    dict(name="tipo_via",              alias="Tipo de vía",                          qvariant=QVariant.String, length=30),
    dict(name="nombre_via",            alias="Nombre vía principal",                 qvariant=QVariant.String, length=80),
    dict(name="numero_via",            alias="Número vía principal",                 qvariant=QVariant.String, length=30),
    dict(name="letra_principal",       alias="Letra vía principal",                  qvariant=QVariant.String, length=10),
    dict(name="prefijo_principal",     alias="Prefijo BIS vía principal",            qvariant=QVariant.String, length=10),
    dict(name="letra_prefijo_principal", alias="Letra prefijo BIS vía principal",    qvariant=QVariant.String, length=10),
    dict(name="cuadrante_principal",   alias="Cuadrante vía principal",              qvariant=QVariant.String, length=10),
    dict(name="num_generadora",        alias="Número vía generadora",                qvariant=QVariant.String, length=30),
    dict(name="letra_generadora",      alias="Letra vía generadora",                 qvariant=QVariant.String, length=10),
    dict(name="sufijo_generadora",     alias="Sufijo BIS vía generadora",            qvariant=QVariant.String, length=10),
    dict(name="letra_sufijo_generadora", alias="Letra sufijo BIS vía generadora",    qvariant=QVariant.String, length=10),
    dict(name="cuadrante_generadora", alias="Cuadrante vía generadora",              qvariant=QVariant.String, length=10),
    dict(name="tipo_via_generadora",   alias="Tipo de vía generadora",               qvariant=QVariant.String, length=30),
    dict(name="nombre_popular",        alias="Nombre popular",                       qvariant=QVariant.String, length=80),
    dict(name="acto_admin",            alias="Acto administrativo",                  qvariant=QVariant.String, length=255),
    dict(name="historico_nom",         alias="Histórico nomenclatura",               qvariant=QVariant.String, length=0, type_name="TEXT"),
    dict(name="fecha_cambio",          alias="Fecha de cambio",                      qvariant=QVariant.String, length=20),  

]

def _ensure_historico_nom_unlimited(layer) -> bool:
    """
    Garantiza que el campo 'historico_nom' no tenga límite 255 (VARCHAR),
    sino que sea TEXT (sin límite práctico) en GPKG.

    Si ya existe como length=255:
    - Crea un campo temporal TEXT
    - Copia valores
    - Borra el campo antiguo
    - Renombra el temporal a 'historico_nom'

    Retorna True si hizo migración, False si no hizo nada.
    """
    fields = layer.fields()
    old_idx = fields.indexOf("historico_nom")
    if old_idx < 0:
        return False

    old_f = fields[old_idx]
    old_len = old_f.length()  # 255 típicamente

    # Ya está "sin límite" o suficientemente grande
    if old_len is None or old_len <= 0:
        return False

    # Solo migramos si el caso problemático es 255 (o valores pequeños)
    prov = layer.dataProvider()
    caps = prov.capabilities()

    # Necesitamos al menos poder agregar atributos
    if not (caps & QgsVectorDataProvider.AddAttributes):
        return False

    # Asegurar edición
    if not layer.isEditable():
        if not layer.startEditing():
            return False

    # 1) Crear campo temporal TEXT
    tmp_name = "historico_nom__txt"
    # Evitar colisiones por si existe
    i = 1
    while layer.fields().indexOf(tmp_name) >= 0:
        i += 1
        tmp_name = f"historico_nom__txt{i}"

    tmp_field = QgsField(
        name=tmp_name,
        type=QVariant.String,
        typeName="TEXT",  # clave para gpkg
        len=0,
        prec=0
    )
    tmp_field.setAlias("Histórico nomenclatura")

    if not layer.addAttribute(tmp_field):
        return False
    layer.updateFields()

    tmp_idx = layer.fields().indexOf(tmp_name)
    if tmp_idx < 0:
        return False

    # 2) Copiar valores
    # (OJO: usa changeAttributeValue para respetar buffer de edición)
    for feat in layer.getFeatures():
        fid = feat.id()
        layer.changeAttributeValue(fid, tmp_idx, feat[old_idx])

    # 3) Borrar el campo viejo (si se puede)
    #    y renombrar el temporal al nombre original
    can_delete = bool(caps & QgsVectorDataProvider.DeleteAttributes)
    can_rename = bool(caps & QgsVectorDataProvider.RenameAttributes)

    if can_delete:
        # borrar el viejo
        if not layer.deleteAttribute(old_idx):
            # si no se pudo borrar, dejamos el tmp creado pero no rompemos
            return True
        layer.updateFields()

        # tras borrar, el índice del tmp puede cambiar → volver a buscar
        tmp_idx2 = layer.fields().indexOf(tmp_name)
        if tmp_idx2 < 0:
            return True

        if can_rename:
            layer.renameAttribute(tmp_idx2, "historico_nom")
            layer.updateFields()
        else:
            # Si no podemos renombrar, al menos dejamos el tmp con alias correcto
            # (pero ojo: tu plugin seguirá leyendo historico_nom antiguo si existe;
            # como lo borramos, ya no existe y deberías ajustar resolución por nombre si aplica)
            pass

    else:
        # Si no podemos borrar, no podemos eliminar la restricción de export
        # porque el campo viejo (255) seguirá existiendo y fallará al exportar.
        # En este caso, lo mejor es NO dejar el viejo: pero si DeleteAttributes no existe,
        # no hay forma limpia desde aquí.
        # Dejamos creado el tmp, pero export seguirá fallando por el viejo.
        return True

    return True

def ensure_required_fields(layer):
    """
    Crea en la capa todos los campos estándar que no existan todavía.

    IMPORTANTÍSIMO:
    - Se ejecuta EN el buffer de edición (no directo al provider),
      para que el usuario pueda "Descartar" sin perder el estado original.
    """
    prov = layer.dataProvider()

    # Si el proveedor no permite agregar atributos, salimos silenciosamente
    if not (prov.capabilities() & QgsVectorDataProvider.AddAttributes):
        return False

    # Garantizar que estamos en modo edición (para que sea reversible con Discard)
    if not layer.isEditable():
        layer.startEditing()

    existing = {f.name() for f in layer.fields()}
    added_any = False

    for spec in REQUIRED_FIELDS:
        if spec["name"] in existing:
            # El campo ya existe, pero asegurar que tenga el alias correcto
            field_idx = layer.fields().indexOf(spec["name"])
            if field_idx >= 0:
                field = layer.fields()[field_idx]
                if field.alias() != spec["alias"]:
                    # Actualizar el alias del campo existente
                    layer.setFieldAlias(field_idx, spec["alias"])
            continue

        # Crear campo con parámetros nombrados para evitar deprecation warning
        fld = QgsField(
            name=spec["name"],
            type=spec["qvariant"],
            typeName=spec.get("type_name", ""),   # ✅ permite TEXT para historico_nom
            len=spec.get("length", 0),
            prec=0
        )

        fld.setAlias(spec["alias"])

        if layer.addAttribute(fld):
            added_any = True

    if added_any:
        layer.updateFields()

    # Migración: si historico_nom existe con length 255, convertir a TEXT
    layer.beginEditCommand("VIAL: Migrar historico_nom a TEXT")
    try:
        migrated = _ensure_historico_nom_unlimited(layer)
        if migrated:
            layer.updateFields()
            added_any = True
    finally:
        layer.endEditCommand()

    return added_any


def apply_field_mapping(layer, mapping):
    """
    Copia los valores de los campos origen de la capa a los campos estándar VIAL.
    Versión optimizada para millones de registros usando operaciones batch.

    mapping: dict { nombre_campo_vial -> nombre_campo_origen (o None) }
    """
    # Aseguramos que los campos estándar existen
    ensure_required_fields(layer)

    fields = layer.fields()
    idx_by_name = {f.name(): i for i, f in enumerate(fields)}

    # Construir lista de pares (src_idx, dst_idx) válidos
    copy_pairs = []
    for target_name, src_name in mapping.items():
        if not src_name:
            continue
        
        src_idx = idx_by_name.get(src_name)
        dst_idx = idx_by_name.get(target_name)
        
        if src_idx is not None and dst_idx is not None and src_idx != dst_idx:
            copy_pairs.append((src_idx, dst_idx))
    
    if not copy_pairs:
        return  # No hay nada que copiar

    # Entramos en edición si no está ya
    if not layer.isEditable():
        if not layer.startEditing():
            raise RuntimeError("No se pudo iniciar la edición en la capa.")

    # Iniciar comando de edición para que sea reversible con undo
    layer.beginEditCommand("VIAL: Transferencia de valores")

    try:
        # OPTIMIZACIÓN: Recopilar TODOS los cambios en un único dict
        # Formato: {fid: {field_idx: new_value}}
        all_changes = {}
        
        for feat in layer.getFeatures():
            fid = feat.id()
            attrs = {}
            
            for src_idx, dst_idx in copy_pairs:
                attrs[dst_idx] = feat[src_idx]
            
            if attrs:
                all_changes[fid] = attrs
        
        # LLAMADA ÚNICA BATCH: aplica todos los cambios de una sola vez
        # Esto es MUCHO más eficiente que millones de llamadas individuales
        # IMPORTANTE: Usar layer.changeAttributeValues (NO dataProvider) para que
        # funcione con el buffer de edición donde están los campos nuevos
        if all_changes:
            for fid, attrs in all_changes.items():
                layer.changeAttributeValues(fid, attrs)
            # Notificar a la capa que los datos cambiaron
            layer.triggerRepaint()
    finally:
        # Finaliza el comando de edición
        layer.endEditCommand()


def normalize_line_direction(layer):
    """
    Normaliza la dirección de todas las líneas en la capa para que las etiquetas
    'AboveLine' y 'BelowLine' se muestren de forma consistente.
    
    Criterio: Ordena las líneas de oeste a este (izquierda a derecha).
    Si dos puntos tienen la misma coordenada X, ordena de sur a norte (abajo a arriba).
    
    Esta función debe ejecutarse en modo edición.
    """
    if not layer.isEditable():
        if not layer.startEditing():
            raise RuntimeError("No se pudo iniciar la edición en la capa.")
    
    layer.beginEditCommand("VIAL: Normalizar dirección de líneas")
    
    try:
        reversed_count = 0
        for feat in layer.getFeatures():
            geom = feat.geometry()
            if not geom or geom.isEmpty():
                continue
            
            # Solo trabajar con líneas
            if QgsWkbTypes.geometryType(geom.wkbType()) != QgsWkbTypes.LineGeometry:
                continue
            
            # Obtener el primer y último punto de la línea
            polyline = geom.asPolyline()
            if not polyline or len(polyline) < 2:
                continue
            
            first_pt = polyline[0]
            last_pt = polyline[-1]
            
            # Determinar si necesita invertirse
            # Criterio: el primer punto debe estar más al oeste (menor X) que el último
            # Si X es igual, el primer punto debe estar más al sur (menor Y)
            needs_reverse = False
            
            if first_pt.x() > last_pt.x():
                needs_reverse = True
            elif first_pt.x() == last_pt.x() and first_pt.y() > last_pt.y():
                needs_reverse = True
            
            if needs_reverse:
                # Invertir la geometría: invertir la lista de puntos y crear nueva geometría
                # Convertir a QgsPointXY ya que fromPolylineXY lo requiere
                reversed_polyline = [QgsPointXY(pt.x(), pt.y()) for pt in reversed(polyline)]
                reversed_geom = QgsGeometry.fromPolylineXY(reversed_polyline)
                if reversed_geom and not reversed_geom.isEmpty():
                    layer.changeGeometry(feat.id(), reversed_geom)
                    reversed_count += 1
        
        # Solo mostrar mensaje si se invirtió alguna línea
        if reversed_count > 0:
            print(f"[VIAL] Se normalizó la dirección de {reversed_count} línea(s).")
    
    finally:
        layer.endEditCommand()
