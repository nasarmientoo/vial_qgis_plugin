"""
vial_reglas.py

Módulo centralizado para:
- Definir los campos VIAL que se guardan en el histórico (historico_nom).
- Definir qué campos activan la detección de calles contiguas.
- Definir la normativa de validación/normalización de campos VIAL.
"""

from typing import List, Tuple, Set, Optional, Dict, Callable
import re

from qgis.core import QgsVectorLayer

# ----------------------------------------------------------------------
# Campos que se guardan en historico_nom
# ----------------------------------------------------------------------
# Cada tupla es: (nombre_en_JSON, nombre_fisico_en_la_capa)
#
# OJO:
# - Para "acto_administrativo" el campo físico se llama "acto_admin".
SNAPSHOT_FIELDS: List[Tuple[str, str]] = [
    ("tipo_via", "tipo_via"),
    ("nombre_via", "nombre_via"),
    ("numero_via", "numero_via"),
    ("letra_principal", "letra_principal"),
    ("prefijo_principal", "prefijo_principal"),
    ("letra_prefijo_principal", "letra_prefijo_principal"),
    ("cuadrante_principal", "cuadrante_principal"),
    ("num_generadora", "num_generadora"),
    ("letra_generadora", "letra_generadora"),
    ("sufijo_generadora", "sufijo_generadora"),
    ("letra_sufijo_generadora", "letra_sufijo_generadora"),
    ("cuadrante_generadora", "cuadrante_generadora"),
    ("tipo_via_generadora", "tipo_via_generadora"),
    ("nombre_popular", "nombre_popular"),
    ("acto_administrativo", "acto_admin"),
]


def get_vial_snapshot_fields_cfg(layer: QgsVectorLayer) -> List[Tuple[str, int]]:
    """
    Devuelve la lista de campos VIAL cuya versión previa queremos guardar
    en 'historico_nom'.

    Cada elemento es:
        (nombre_en_JSON, índice_del_campo_en_la_capa)

    Si un campo no existe en la capa, el índice será -1.
    """
    if layer is None:
        return []

    fields = layer.fields()
    cfg: List[Tuple[str, int]] = []

    for json_name, field_name in SNAPSHOT_FIELDS:
        idx = fields.indexOf(field_name) if fields else -1
        cfg.append((json_name, idx))

    return cfg


# ----------------------------------------------------------------------
# Campos que activan las sugerencias de calles contiguas
# ----------------------------------------------------------------------

WATCHED_FIELDS = [
    "tipo_via",
    "nombre_via",
    "numero_via",
    "letra_principal",
    "prefijo_principal",
    "letra_prefijo_principal",
    "cuadrante_principal", 
    "nombre_popular",
    "acto_admin",
]


def get_watched_attr_idxs(layer: QgsVectorLayer) -> Set[int]:
    """
    Devuelve el conjunto de índices de campos que deben activar:
    - el botón "Identificar calles contiguas"
    - el tracking de self._dirty_fids

    Solo incluye los campos que realmente existan en la capa.
    """
    if layer is None:
        return set()

    fields = layer.fields()
    watched: Set[int] = set()

    for name in WATCHED_FIELDS:
        idx = fields.indexOf(name)
        if idx != -1:
            watched.add(idx)

    return watched


# ----------------------------------------------------------------------
# Normativa y validadores por campo
# ----------------------------------------------------------------------

# Catálogo de tipos de vía (abreviatura de 2 caracteres + descripción)
# Puedes ampliarlo según tu necesidad.
TIPO_VIA_CHOICES: List[Tuple[str, str]] = [
    (None, "<sin asignar>"),
    ("CL", "Calle"),
    ("KR", "Carrera"),
    ("DG", "Diagonal"),
    ("TV", "Transversal"),
    ("AV", "Avenida"),
    ("AC", "Avenida Calle"),
    ("AK", "Avenida Carrera"),
    ("CT", "Carretera"),
    ("VT", "Variante"),
    ("TR", "Troncal"),
]

TIPO_VIA_CODES = {code for code, _ in TIPO_VIA_CHOICES if code is not None}

# Cuadrantes disponibles (N, S, E, W)
CUADRANTE_CHOICES: List[Tuple[str, str]] = [
    (None, "<sin asignar>"),
    ("N", "Norte"),
    ("S", "Sur"),
    ("E", "Este"),
    ("W", "Oeste"),
]

CUADRANTE_CODES = {code for code, _ in CUADRANTE_CHOICES if code is not None}
LETRAS_PROHIBIDAS_BOGOTA = {"E", "S", "W", "Ñ"}

# Valores que se deben tratar como "no aplica" / vacío
NA_STRINGS = {
    "NO APLICA",
    "N/A",
    "NA",
    "NO APLICA.",
    "NO APLICA ",
    "NO APLICA-",
    "NO APLICA -",
}


def _normalize_upper(value: Optional[object]) -> Optional[str]:
    """
    Convierte a str, hace strip y pasa a MAYÚSCULAS.
    Devuelve None si queda vacío.
    """
    if value is None:
        return None
    txt = str(value).strip()
    if not txt:
        return None
    return txt.upper()


def _normalize_na(value: Optional[object]) -> Optional[str]:
    """
    Aplica normalización a mayúsculas y, si el valor está en NA_STRINGS,
    lo convierte en None.
    """
    txt = _normalize_upper(value)
    if txt is None:
        return None
    if txt in NA_STRINGS:
        return None
    return txt


# ---------------- tipo_via ----------------

def validate_and_normalize_tipo_via(raw: Optional[object]) -> Tuple[Optional[str], Optional[str]]:
    """
    Reglas:
    - Campo con catálogo cerrado (CL, KR, DG, TV, AV, ...).
    - Permite variaciones comunes como 'CALLE' -> 'CL', 'CARRERA'/'CRA' -> 'KR'.
    """
    val = _normalize_na(raw)
    if val is None:
        return None, None  # permitir vacío

    if val in TIPO_VIA_CODES:
        return val, None

    # Correcciones típicas
    map_correccion = {
        "CALLE": "CL",
        "CRA": "KR",
        "CARRERA": "KR",
        "AVENIDA": "AV",
        "AV.": "AV",
    }
    if val in map_correccion:
        return map_correccion[val], None

    # No coincide con el catálogo
    return val, "Tipo de vía no válido. Use el catálogo (CL, KR, DG, TV, AV, …)."


# ---------------- numero_via ----------------

def validate_and_normalize_numero_via(raw: Optional[object]) -> Tuple[Optional[str], Optional[str]]:
    """
    Reglas:
    - Entero > 0.
    - Sin ceros a la izquierda.
    - No se permite '0'.
    - Si no es número ⇒ error.
    """
    val = _normalize_na(raw)
    if val is None:
        return None, None

    if not val.isdigit():
        return val, "El número de vía solo admite dígitos (0–9)."

    if val == "0":
        return val, "El número de vía debe ser mayor que 0."

    # Quitar ceros a la izquierda
    return str(int(val)), None


# ---------------- letras (principal y de BIS) ----------------

_LETRA_PATTERN = re.compile(r"^[A-Z]{1,3}$")


def validate_and_normalize_letra_principal(raw: Optional[object]) -> Tuple[Optional[str], Optional[str]]:
    """
    Reglas:
    - 1 caracter alfanuméricos, sin espacios.
    - Letras A–Z y dígitos 0–9.
    - No se permiten E, S, O, Ñ (regla Bogotá).
    """
    val = _normalize_na(raw)
    if val is None:
        return None, None

    if len(val) > 1:
        return val, "La letra de la vía debe 1 caracter."

    if not _LETRA_PATTERN.match(val):
        return val, "La letra de la vía solo admite letras A–Z (sin números ni espacios)."

    for ch in val:
        if ch in LETRAS_PROHIBIDAS_BOGOTA:
            return val, "No se permiten las letras E, S, O ni Ñ para letras de vía."

    return val, None


# ---------------- prefijo_principal (BIS) ----------------

def validate_and_normalize_prefijo_principal(raw: Optional[object]) -> Tuple[Optional[str], Optional[str]]:
    """
    Reglas (simple):
    - Vacío permitido (None).
    - Único valor permitido: BIS (en cualquier combinación de may/min).
    - Normaliza a 'BIS'.
    - Opcional: acepta 'BIS.' y lo normaliza a 'BIS'.
    """
    val = _normalize_na(raw)
    if val is None:
        return None, None  # permitir vacío

    # Normalizaciones tolerantes
    val = val.strip().upper()
    if val.endswith("."):
        val = val[:-1].strip()  # 'BIS.' -> 'BIS'

    if val == "BIS":
        return "BIS", None

    return val, "El único prefijo permitido en 'prefijo_principal' es BIS (o dejarlo vacío)."



# ---------------- acto_admin ----------------

def validate_and_normalize_acto_admin(raw: Optional[object]) -> Tuple[Optional[str], Optional[str]]:
    """
    Reglas:
    - Referencia a decreto, resolución, acuerdo, etc.
    - Formato sugerido: [ENTIDAD] [TIPO] [NÚMERO]/[AÑO]
    - Sin tildes ni signos raros: solo letras A–Z, números, espacio y '/'.
    """
    val = _normalize_na(raw)
    if val is None:
        return None, None

    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 /")
    for ch in val:
        if ch not in allowed:
            return val, "En 'acto_admin' solo se permiten letras, números, espacio y '/'."

    return val, None


# ---------------- nombre_via / nombre_popular ----------------

# Signos de puntuación que se quieren prohibir en los nombres
BAD_PUNCTUATION = set(".,;:#!?\"'()[]{}<>|\\_+-=")


def _validate_nombre_generic(raw: Optional[object]) -> Tuple[Optional[str], Optional[str]]:
    """
    Reglas comunes para nombre_via y nombre_popular:
    - Opcional.
    - MAYÚSCULAS.
    - No usar signos de puntuación (solo espacio).
    - No debe empezar por CL, KR, DG, etc. (eso es tipo_via).
    - Caso especial: 'N.Q.S.' → normalizar a 'NORTE QUITO SUR'.
    """
    val = _normalize_na(raw)
    if val is None:
        return None, None

    # Detectar N.Q.S / NQS → NORTE QUITO SUR
    clean = val.replace(".", "").replace(" ", "")
    if clean == "NQS":
        return "NORTE QUITO SUR", None

    # Prohibir signos de puntuación (excepto espacio)
    for ch in val:
        if ch in BAD_PUNCTUATION:
            return val, "No se permiten signos de puntuación en el nombre (solo espacios)."

    # No debe empezar por tipo de vía
    # p.ej. 'CL 10 CARACAS' → error: CL va en tipo_via, no en nombre_via
    upper_val = val.upper()
    for code in TIPO_VIA_CODES:
        prefix = code + " "
        if upper_val.startswith(prefix):
            return val, "El tipo de vía (CL, KR, etc.) no debe ir en el nombre; use el campo 'tipo_via'."

    return upper_val, None


def validate_and_normalize_nombre_via(raw: Optional[object]) -> Tuple[Optional[str], Optional[str]]:
    return _validate_nombre_generic(raw)


def validate_and_normalize_nombre_popular(raw: Optional[object]) -> Tuple[Optional[str], Optional[str]]:
    return _validate_nombre_generic(raw)


# ----------------------------------------------------------------------
# Mapa central de validadores por nombre de campo
# ----------------------------------------------------------------------

FIELD_VALIDATORS: Dict[str, Callable[[Optional[object]], Tuple[Optional[str], Optional[str]]]] = {
    "tipo_via": validate_and_normalize_tipo_via,
    "numero_via": validate_and_normalize_numero_via,
    "letra_principal": validate_and_normalize_letra_principal,
    "prefijo_principal": validate_and_normalize_prefijo_principal,
    "letra_prefijo_principal": validate_and_normalize_letra_principal,
    "acto_admin": validate_and_normalize_acto_admin,
    "nombre_via": validate_and_normalize_nombre_via,
    "nombre_popular": validate_and_normalize_nombre_popular,
}
