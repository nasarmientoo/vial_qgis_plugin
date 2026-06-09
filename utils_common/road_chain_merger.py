# =============================
# Archivo: road_chain_merger.py (py39-safe)
# Devuelve SOLO la capa final disuelta.
# Pensado para usarse al final del flujo Sat2Graph y Calculo de Malla Vial con Base Predial: recibe una capa de líneas,
# calcula cadenas por alineación y devuelve una capa temporal "Raster_calles_union" o "Predial_calles_union".
# =============================
from collections import defaultdict, deque
from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsVectorLayer, QgsField, QgsFeature, QgsWkbTypes, QgsProject, edit
)
import processing


def _geometry_uri_from_layer(src_layer):
    """
    Construye un URI de geometría (tipo y CRS) basado en la capa fuente
    para crear capas en memoria que conserven Z/M y CRS.
    """
    wkb = src_layer.wkbType()
    base = "MultiLineString" if QgsWkbTypes.isMultiType(wkb) else "LineString"
    if QgsWkbTypes.hasZ(wkb) and QgsWkbTypes.hasM(wkb): geom = base + "ZM"
    elif QgsWkbTypes.hasZ(wkb):                          geom = base + "Z"
    elif QgsWkbTypes.hasM(wkb):                          geom = base + "M"
    else:                                                geom = base
    return f"{geom}?crs={src_layer.crs().authid()}"


def _copy_layer_temp(src_layer, out_name):
    """
    Copia completa de una capa a memoria (mismo esquema y geometría).
    No añade la capa al proyecto; devuelve la referencia en memoria.
    """
    uri = _geometry_uri_from_layer(src_layer)
    out = QgsVectorLayer(uri, out_name, "memory")
    dp = out.dataProvider()
    dp.addAttributes(src_layer.fields())
    out.updateFields()
    dp.addFeatures(list(src_layer.getFeatures()))
    out.updateExtents()
    return out


def _ensure_integer_id_field(src_lines, id_name="id"):
    """
    Garantiza campo entero 'id'. Si ya existe, NO copiamos la capa:
    devolvemos src_lines tal cual (evita addFeatures extra).
    """
    if src_lines.fields().indexOf(id_name) != -1:
        return src_lines

    res = processing.run("native:fieldcalculator", {
        "INPUT": src_lines,
        "FIELD_NAME": id_name,
        "FIELD_TYPE": 1,  # Entero
        "FIELD_LENGTH": 12,
        "FIELD_PRECISION": 0,
        "NEW_FIELD": True,
        "FORMULA": "to_int($id)",
        "OUTPUT": "TEMPORARY_OUTPUT"
    })
    return res["OUTPUT"]


def _add_axis_deg(lines_with_id, field_name="axis_deg"):
    """
    Calcula la orientación del segmento (en grados) independiente del sentido,
    normalizada al intervalo [0, 180). Se basa en line_merge($geometry).
    """
    expr = "(degrees(azimuth(start_point(line_merge($geometry)), end_point(line_merge($geometry)))) + 360) % 180"
    res = processing.run("native:fieldcalculator", {
        "INPUT": lines_with_id,
        "FIELD_NAME": field_name,
        "FIELD_TYPE": 0,  # Doble
        "FIELD_LENGTH": 20,
        "FIELD_PRECISION": 6,
        "NEW_FIELD": True,
        "FORMULA": expr,
        "OUTPUT": "TEMPORARY_OUTPUT"
    })
    return res["OUTPUT"]


def _snap_lines(lines_with_axis, tol=1.0):
    """
    Realiza 'snap' entre líneas de la misma capa para alinear nodos e insertar
    vértices donde corresponda. Facilita la detección de intersecciones.
    """
    res = processing.run("native:snapgeometries", {
        "INPUT": lines_with_axis,
        "REFERENCE_LAYER": lines_with_axis,
        "TOLERANCE": tol,
        "BEHAVIOR": 2,  # Preferir alinear nodos; insertar vértices
        "OUTPUT": "TEMPORARY_OUTPUT"
    })
    return res["OUTPUT"]


def _line_intersections(snapped):
    """
    Calcula intersecciones línea-línea sobre la capa ya 'snappeada'.
    Trae 'id' y 'axis_deg' de ambas partes; la segunda con prefijo '_2'.
    """
    res = processing.run("native:lineintersections", {
        "INPUT": snapped,
        "INTERSECT": snapped,
        "INPUT_FIELDS": ["id", "axis_deg"],
        "INTERSECT_FIELDS": ["id", "axis_deg"],
        "INPUT_FIELDS_PREFIX": "",
        "INTERSECT_FIELDS_PREFIX": "_2",
        "OUTPUT": "TEMPORARY_OUTPUT"
    })
    return res["OUTPUT"]


def _add_axisdiff_and_ids(inters):
    """
    Genera una copia en memoria de la tabla de intersecciones y añade:
      - axis_diff: diferencia angular plegada a [0, 90]
      - a_id, b_id: ids ordenados (menor, mayor) para definir parejas canónicas
    Si no encuentra los campos de contraparte, devuelve la entrada tal cual.
    """
    fields = [f.name() for f in inters.fields()]

    def _guess(fields, base):
        for name in (f"{base}_2", f"_2{base}", f"2_{base}"):
            if name in fields:
                return name
        return None

    id_b   = _guess(fields, "id")
    axis_b = _guess(fields, "axis_deg")
    if not id_b or not axis_b:
        # No hay campos de contraparte; salir sin cambios (no habrá fusión)
        return inters

    # Construir copia en memoria con nuevos campos
    out = QgsVectorLayer(f"Point?crs={inters.crs().authid()}", "intersections_aug", "memory")
    dp = out.dataProvider()
    new_fields = list(inters.fields())
    new_fields += [
        QgsField("axis_diff", QVariant.Double),
        QgsField("a_id", QVariant.Int),
        QgsField("b_id", QVariant.Int)
    ]
    dp.addAttributes(new_fields)
    out.updateFields()

    idx_id_a = inters.fields().indexOf("id")
    idx_id_b = inters.fields().indexOf(id_b)
    idx_ax_a = inters.fields().indexOf("axis_deg")
    idx_ax_b = inters.fields().indexOf(axis_b)

    feats = []
    for f in inters.getFeatures():
        attrs = f.attributes()
        ida = attrs[idx_id_a] if idx_id_a >= 0 else None
        idb = attrs[idx_id_b] if idx_id_b >= 0 else None
        axa = attrs[idx_ax_a] if idx_ax_a >= 0 else None
        axb = attrs[idx_ax_b] if idx_ax_b >= 0 else None

        # Orden canónico (a_id <= b_id)
        try:
            ia, ib = int(ida), int(idb)
            a_id, b_id = (ia, ib) if ia <= ib else (ib, ia)
        except Exception:
            a_id = b_id = None

        # Diferencia angular plegada
        try:
            axis_diff = abs(((float(axa) - float(axb) + 90) % 180) - 90)
        except Exception:
            axis_diff = None

        g = QgsFeature(out.fields())
        g.setGeometry(f.geometry())
        g.setAttributes(attrs + [axis_diff, a_id, b_id])
        feats.append(g)

    if feats:
        dp.addFeatures(feats)
        out.updateExtents()
    return out


def _filter_intersections_by_angle(inters_aug, thresh_deg):
    """
    Filtra intersecciones que están alineadas (axis_diff <= umbral) y elimina
    autopares (a_id == b_id) o valores nulos.
    """
    expr = f"\"axis_diff\" <= {thresh_deg} AND \"a_id\" <> \"b_id\" AND \"a_id\" IS NOT NULL AND \"b_id\" IS NOT NULL"
    res = processing.run("native:extractbyexpression", {
        "INPUT": inters_aug,
        "EXPRESSION": expr,
        "OUTPUT": "TEMPORARY_OUTPUT"
    })
    return res["OUTPUT"]


def _build_pairs_long(filtered_pts_layer):
    """
    Construye una tabla en memoria con el esquema:
      - line_id (Int)
      - pair_key (Texto) con formato 'minid_maxid'
    Emite dos filas por pareja (una por cada extremo) para facilitar uniones.
    """
    out = QgsVectorLayer("None?field=line_id:integer&field=pair_key:string(40)", "pairs_long", "memory")
    dp = out.dataProvider()

    seen = set()
    feats = []
    for f in filtered_pts_layer.getFeatures():
        a = f["a_id"]; b = f["b_id"]
        if a is None or b is None:
            continue
        try:
            a = int(a); b = int(b)
        except Exception:
            continue
        if a == b:
            continue
        lo, hi = (a, b) if a < b else (b, a)
        key = f"{lo}_{hi}"
        if key not in seen:
            seen.add(key)
        # Dos filas por pareja (bidireccional)
        for rid in (lo, hi):
            g = QgsFeature(out.fields())
            g.setAttributes([rid, key])
            feats.append(g)

    if feats:
        dp.addFeatures(feats)
        out.updateExtents()
    return out


def _compute_chains(snapped_lines, pairs_long, id_field="id"):
    """
    Asigna 'chain_id' por componente conexo en el grafo de líneas alineadas.
    - Nodos: ids de líneas
    - Aristas: pair_key (parejas alineadas)
    Los segmentos sin pares (aislados) reciben su propio 'chain_id'.
    """
    # Mapear pair_key -> conjunto de ids
    pk_to_ids = defaultdict(set)
    for f in pairs_long.getFeatures():
        pk = f["pair_key"]; rid = f["line_id"]
        if pk and rid is not None:
            try:
                pk_to_ids[str(pk)].add(int(rid))
            except Exception:
                pass

    # Adyacencia no dirigida
    adj = defaultdict(set)
    for ids in pk_to_ids.values():
        ids = list(ids)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                u, v = ids[i], ids[j]
                if u != v:
                    adj[u].add(v)
                    adj[v].add(u)

    # Todos los ids (para asignar cadenas a aislados también)
    all_ids = set()
    for feat in snapped_lines.getFeatures():
        try:
            all_ids.add(int(feat[id_field]))
        except Exception:
            pass

    # Componentes conexos (BFS)
    comp = {}
    cid = 1
    for node in list(adj.keys()):
        if node in comp:
            continue
        q = deque([node])
        comp[node] = cid
        while q:
            cur = q.popleft()
            for nei in adj[cur]:
                if nei not in comp:
                    comp[nei] = cid
                    q.append(nei)
        cid += 1

    # Aislados: asignar un 'chain_id' único
    for rid in sorted(all_ids):
        if rid not in comp:
            comp[rid] = cid
            cid += 1

    # Escribir 'chain_id' en una copia temporal
    merged = _copy_layer_temp(snapped_lines, "roads_chain_merged")
    with edit(merged):
        if merged.fields().indexOf("chain_id") == -1:
            merged.addAttribute(QgsField("chain_id", QVariant.Int))
        idx_chain = merged.fields().indexOf("chain_id")
        for feat in merged.getFeatures():
            try:
                rid = int(feat[id_field])
            except Exception:
                rid = None
            merged.changeAttributeValue(
                feat.id(),
                idx_chain,
                int(comp.get(rid, 0)) if rid is not None else 0
            )
    return merged


def _dissolve_by_chain(roads_chain_merged):
    """
    Disuelve por 'chain_id' para obtener una geometría por cadena (línea unida).
    Devuelve una capa temporal en memoria.
    """
    res = processing.run("native:dissolve", {
        "INPUT": roads_chain_merged,
        "FIELD": ["chain_id"],
        "SEPARATE_DISJOINT": False,
        "OUTPUT": "TEMPORARY_OUTPUT"
    })
    return res["OUTPUT"]

def compute_chains_layer(
    lines_layer: QgsVectorLayer,
    snap_tol_m: float = 1.0,
    angle_thresh_deg: float = 15.0,
    id_field: str = "id",
) -> QgsVectorLayer:
    """
    Igual que merge_lines_to_dissolved pero NO disuelve las líneas.

    Devuelve una copia temporal de la capa de entrada con:
      - un campo entero `id` (si no existía) con to_int($id)
      - un campo entero `chain_id` que agrupa calles contiguas/alineadas.

    Esta capa es solo de trabajo en memoria; no se añade al proyecto.
    """
    # 1) Asegurar id entero
    with_id = _ensure_integer_id_field(lines_layer, id_name=id_field)

    # 2) orientación
    with_axis = _add_axis_deg(with_id, field_name="axis_deg")

    # 3) snap
    snapped = _snap_lines(with_axis, tol=snap_tol_m)

    # 4) corregir posibles geometrías inválidas generadas por el snap
    snapped_fixed = processing.run("native:fixgeometries", {
        "INPUT": snapped,
        "OUTPUT": "TEMPORARY_OUTPUT"
    })["OUTPUT"]

    # 5) intersecciones
    inters = _line_intersections(snapped_fixed)

    # 6) métricas en intersecciones
    inters_aug = _add_axisdiff_and_ids(inters)

    # 7) filtro angular
    filtered = _filter_intersections_by_angle(inters_aug, angle_thresh_deg)

    # 8) chain_id
    roads_chain_merged = _compute_chains(
        snapped_fixed,
        pairs_long=_build_pairs_long(filtered),
        id_field=id_field
    )

    return roads_chain_merged


def merge_lines_to_dissolved(
    lines_layer: QgsVectorLayer,
    snap_tol_m: float = 1.0,
    angle_thresh_deg: float = 15.0,
    final_name: str = "Raster_calles_union",
) -> QgsVectorLayer:
    """
    Flujo extremo a extremo:
      1) Asegurar campo entero 'id'
      2) Calcular 'axis_deg' (orientación)
      3) Snap de vértices (tolerancia en metros)
      4) Intersecciones línea-línea
      5) Calcular 'axis_diff', 'a_id', 'b_id'
      6) Filtrar intersecciones alineadas (<= umbral angular)
      7) Construir pares (tabla larga)
      8) Calcular 'chain_id' por componente conexo
      9) Disolver por 'chain_id'

    No añade capas intermedias al proyecto. Devuelve solo la capa disuelta.
    """
    # 1) id entero
    with_id = _ensure_integer_id_field(lines_layer, id_name="id")
    # 2) orientación
    with_axis = _add_axis_deg(with_id, field_name="axis_deg")
    # 3) snap
    snapped = _snap_lines(with_axis, tol=snap_tol_m)
    # 4) intersecciones
    inters = _line_intersections(snapped)
    # 5) métricas en intersecciones
    inters_aug = _add_axisdiff_and_ids(inters)
    # 6) filtro angular
    filtered = _filter_intersections_by_angle(inters_aug, angle_thresh_deg)
    # 7) pares
    pairs_long = _build_pairs_long(filtered)
    # 8) chain_id
    roads_chain_merged = _compute_chains(snapped, pairs_long, id_field="id")
    # 9) disolver
    dissolved = _dissolve_by_chain(roads_chain_merged)
    dissolved.setName(final_name)
    return dissolved
