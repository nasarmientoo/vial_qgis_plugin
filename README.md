# Road Arc Attribute Editor (Editor de Atributos de Arco Vial)

A QGIS plugin to edit, validate, and normalize road arc nomenclature attributes following the Colombian road naming standard (IGAC / DANE).

## Description

This plugin provides a specialized workflow for standardizing the attribute table of road network layers. It is designed for GIS analysts working with Colombian urban or rural road datasets that need to conform to the official nomenclature schema.

**Key features:**

- **Field mapping dialog** — Map your existing layer fields to the 17 standard VIAL fields (road type, main road number, quadrant, generator road, administrative act, etc.).
- **Attribute editor dock** — An inline attribute table showing only the VIAL fields, with:
  - Dropdown (combobox) editors for controlled-vocabulary fields (`tipo_via`, `cuadrante_principal`, `cuadrante_generadora`).
  - Per-field validation with automatic normalization (uppercase letters, trimming, numeric checks).
  - Multi-row editing: edit one cell and propagate the value to all selected rows.
  - Filtering by "all features", "selected features", or "visible in map" modes.
- **Contiguous street chain detection** — Automatically groups geometrically contiguous and aligned road arcs into chains, suggests a single consistent nomenclature per chain, and highlights conflicts.
- **Generator road calculator** (`vía generadora`) — Calculates and populates the generator road fields from the spatial relationships in the network.
- **Automatic labeling** — Enables dual map labels (main road above the line, generator road below) with color-coded text upon starting an editing session.
- **Full undo/redo support** — All operations use QGIS edit buffers and named edit commands, so every action can be undone from the standard QGIS undo stack.

## Requirements

- **QGIS** ≥ 3.22 (LTR or later)
- **Python** ≥ 3.9 (bundled with QGIS)
- No additional Python packages are required. The plugin uses only libraries bundled with QGIS: `PyQt5`, `qgis.core`, `qgis.gui`, and `qgis.PyQt`.

> If you are on Windows and need to install additional Python packages in the future, refer to the guide [Installing Python packages in QGIS 3 (for Windows)](https://landscapearchaeology.org/2018/installing-python-packages-in-qgis-3-for-windows/).

## Installation

### From the QGIS Plugin Repository (recommended)

1. Open QGIS.
2. Go to **Plugins → Manage and Install Plugins…**
3. Search for **"Editor de Atributos de Arco Vial"**.
4. Click **Install Plugin**.

### Manual installation

1. Download the latest `.zip` from the [Releases page](https://github.com/nasarmientoo/vial_qgis_plugin/releases).
2. In QGIS go to **Plugins → Manage and Install Plugins… → Install from ZIP**.
3. Select the downloaded `.zip` and click **Install Plugin**.

Alternatively, unzip the package and copy the `att_editor_plugin/` folder into your QGIS plugins directory:

| Platform | Path |
|----------|------|
| Windows  | `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\` |
| Linux    | `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/` |
| macOS    | `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/` |

Then enable the plugin under **Plugins → Manage and Install Plugins… → Installed**.

## Usage

1. Load a line vector layer representing your road network.
2. Go to **Plugins → Editor Atributos Vial → Editor atributos de arco**, or click the toolbar button.
3. In the **Field Mapping dialog**, select your road layer and map each standard VIAL field to the corresponding field in your layer (leave unmapped fields blank).
4. Click **OK**. The plugin will:
   - Create any missing standard fields in the layer (in edit mode, fully reversible).
   - Copy values from your original fields to the standard fields.
   - Normalize line directions for consistent labeling.
   - Enable dual map labels.
   - Open the **Attribute Editor dock**.
5. In the dock, edit attributes directly in the table. Use the toolbar buttons to select, filter, zoom, and pan to features.
6. Click **Identificar calles contiguas** to detect chains of streets with the same nomenclature and review suggestions in the suggestions panel.
7. Double-click a suggestion row to see per-segment details and choose which segments receive the suggested values.
8. Click **✓ Aplicar sugerencias seleccionadas** or **✓ Aceptar todas las sugerencias** to apply.
9. When all fields with numeric `numero_via` values are filled, the **Calcular vía generadora** button activates. Click it to automatically compute generator road fields.
10. Save edits with the standard QGIS **Save Layer Edits** button or from the layer context menu.

## Standard VIAL Fields

The plugin creates and manages the following 17 fields:

| Field name | Alias | Description |
|---|---|---|
| `tipo_via` | Tipo de vía | Road type code (CL, KR, TV, DG, …) |
| `nombre_via` | Nombre vía principal | Main road name |
| `numero_via` | Número vía principal | Main road number |
| `letra_principal` | Letra vía principal | Letter suffix of main road |
| `prefijo_principal` | Prefijo BIS vía principal | BIS prefix of main road |
| `letra_prefijo_principal` | Letra prefijo BIS vía principal | BIS prefix letter |
| `cuadrante_principal` | Cuadrante vía principal | Quadrant (Norte, Sur, Este, Oeste) |
| `num_generadora` | Número vía generadora | Generator road number |
| `letra_generadora` | Letra vía generadora | Generator road letter |
| `sufijo_generadora` | Sufijo BIS vía generadora | Generator road BIS suffix |
| `letra_sufijo_generadora` | Letra sufijo BIS vía generadora | Generator road BIS suffix letter |
| `cuadrante_generadora` | Cuadrante vía generadora | Generator road quadrant |
| `tipo_via_generadora` | Tipo de vía generadora | Generator road type |
| `nombre_popular` | Nombre popular | Popular/colloquial road name |
| `acto_admin` | Acto administrativo | Administrative act reference |
| `historico_nom` | Histórico nomenclatura | Nomenclature change history (JSON) |
| `fecha_cambio` | Fecha de cambio | Date of last nomenclature change |

## License

This plugin is released under the **GNU General Public License v2.0 or later (GPL-2.0-or-later)**.

See the [LICENSE](LICENSE) file for the full license text.

This plugin uses the following libraries, all of which are GPL-compatible:

- **QGIS / PyQGIS** — GPL-2.0-or-later
- **PyQt5** — GPL-3.0
- **Python standard library** — PSF License

## Repository and issue tracker

- **Source code:** [https://github.com/nasarmientoo/vial_qgis_plugin](https://github.com/nasarmientoo/vial_qgis_plugin)
- **Bug reports and feature requests:** [https://github.com/nasarmientoo/vial_qgis_plugin/issues](https://github.com/nasarmientoo/vial_qgis_plugin/issues)

## Author

Nataly Sarmiento Ospina — [nasarmientoo@gmail.com](mailto:nasarmientoo@gmail.com)
