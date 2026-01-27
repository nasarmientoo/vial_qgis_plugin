# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QVariant

# Campos obligatorios estandarizados (Tool 2)
# label: lo que ve el usuario en el mapeo
# name: nombre real del campo en la capa
# type: tipo de dato
REQUIRED_FIELDS = [
    {"label": "Tipo de vía",              "name": "tipo_via",                  "type": QVariant.String},
    {"label": "Nombre vía principal",     "name": "nombre_via_principal",      "type": QVariant.String},
    {"label": "Número vía principal",     "name": "numero_via_principal",      "type": QVariant.Int},
    {"label": "Letra vía principal",      "name": "letra_via_principal",       "type": QVariant.String},
    {"label": "Prefijo BIS vía principal","name": "prefijo_bis_via_principal", "type": QVariant.String},
    {"label": "Letra prefijo BIS",        "name": "letra_prefijo_bis",         "type": QVariant.String},
    {"label": "Cuadrante vía principal",  "name": "cuadrante_via_principal",   "type": QVariant.String},
    {"label": "Número vía secundaria",    "name": "numero_via_secundaria",     "type": QVariant.Int},
    {"label": "Letra vía secundaria",     "name": "letra_via_secundaria",      "type": QVariant.String},
    {"label": "Sufijo BIS vía secundaria","name": "sufijo_bis_via_secundaria", "type": QVariant.String},
    {"label": "Letra sufijo BIS",         "name": "letra_sufijo_bis",          "type": QVariant.String},
    {"label": "Histórico",                "name": "historico",                 "type": QVariant.String},
    {"label": "Fecha de cambio",          "name": "fecha_cambio",              "type": QVariant.Date},
]
