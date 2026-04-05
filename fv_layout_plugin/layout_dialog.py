# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import Optional

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QSpinBox,
    QVBoxLayout,
)
from qgis.core import QgsMapLayerProxyModel, QgsProject, QgsWkbTypes
from qgis.gui import QgsMapLayerComboBox


@dataclass
class FvLayoutParameters:
    lots_layer_id: str
    excluded_layer_id: Optional[str]
    dtm_layer_id: str
    selected_only: bool
    fence_offset_m: float
    road_width_m: float
    module_road_clearance_m: float
    slope_limit_deg: float
    structure_type: str
    table_length_m: float
    table_width_m: float
    modules_per_table: int
    module_power_kw: float
    table_power_kw: float
    row_pitch_m: float
    table_spacing_m: float
    azimuth_min_deg: float
    azimuth_max_deg: float
    azimuth_step_deg: float
    shift_mode: str
    shift_value_m: float
    shift_min_m: float
    shift_max_m: float
    shift_step_m: float
    output_dir: str
    output_prefix: str
    add_to_project: bool


class LayoutDialog(QDialog):
    SHIFT_NONE = "none"
    SHIFT_ALTERNATED = "alternated"
    SHIFT_OPTIMIZED = "optimized"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FV Layout Auto Designer")
        self.resize(760, 820)
        self._build_ui()
        self._wire_signals()

    def _build_ui(self):
        root = QVBoxLayout(self)

        root.addWidget(self._build_input_group())
        root.addWidget(self._build_rules_group())
        root.addWidget(self._build_structure_group())
        root.addWidget(self._build_optimization_group())
        root.addWidget(self._build_output_group())
        root.addWidget(self._build_execution_group())

    def _new_distance(self, min_val=0.0, max_val=10000.0, default=1.0, dec=2):
        spin = QDoubleSpinBox()
        spin.setRange(min_val, max_val)
        spin.setDecimals(dec)
        spin.setValue(default)
        spin.setSingleStep(0.5)
        return spin

    def _build_input_group(self):
        group = QGroupBox("Sezione A – Input")
        lay = QFormLayout(group)

        self.lots_combo = QgsMapLayerComboBox()
        self.lots_combo.setFilters(QgsMapLayerProxyModel.PolygonLayer)

        self.excluded_combo = QgsMapLayerComboBox()
        self.excluded_combo.setAllowEmptyLayer(True)
        self.excluded_combo.setFilters(QgsMapLayerProxyModel.PolygonLayer)

        self.dtm_combo = QgsMapLayerComboBox()
        self.dtm_combo.setFilters(QgsMapLayerProxyModel.RasterLayer)

        self.selected_only_chk = QCheckBox("Usa solo elementi selezionati")

        lay.addRow("Layer lotti catastali", self.lots_combo)
        lay.addRow("Layer aree escluse (opzionale)", self.excluded_combo)
        lay.addRow("Raster DTM", self.dtm_combo)
        lay.addRow("", self.selected_only_chk)
        return group

    def _build_rules_group(self):
        group = QGroupBox("Sezione B – Distanze e regole")
        lay = QFormLayout(group)

        self.fence_offset = self._new_distance(default=2.0)
        self.road_width = self._new_distance(default=4.0)
        self.module_clearance = self._new_distance(default=1.0)
        self.slope_limit = self._new_distance(max_val=89.0, default=12.0)

        lay.addRow("Distanza recinzione dal confine (m)", self.fence_offset)
        lay.addRow("Larghezza viabilità perimetrale (m)", self.road_width)
        lay.addRow("Distanza minima moduli da viabilità (m)", self.module_clearance)
        lay.addRow("Soglia massima pendenza (°)", self.slope_limit)
        return group

    def _build_structure_group(self):
        group = QGroupBox("Sezione C – Struttura FV")
        lay = QFormLayout(group)

        self.structure_combo = QComboBox()
        self.structure_combo.addItem("Tracker", "tracker")
        self.structure_combo.addItem("Struttura fissa", "fixed")

        self.table_length = self._new_distance(default=18.0)
        self.table_width = self._new_distance(default=4.0)
        self.modules_per_table = QSpinBox()
        self.modules_per_table.setRange(1, 999)
        self.modules_per_table.setValue(40)

        self.module_power = self._new_distance(max_val=5.0, default=0.7, dec=4)
        self.table_power = self._new_distance(max_val=9999.0, default=0.0, dec=4)

        self.row_pitch = self._new_distance(default=8.0)
        self.table_spacing = self._new_distance(default=0.5)

        lay.addRow("Tipologia struttura", self.structure_combo)
        lay.addRow("Lunghezza tavolo (m)", self.table_length)
        lay.addRow("Larghezza tavolo (m)", self.table_width)
        lay.addRow("Moduli per tavolo", self.modules_per_table)
        lay.addRow("Potenza modulo (kW)", self.module_power)
        lay.addRow("Potenza tavolo opzionale (kW, 0 = calcolo)", self.table_power)
        lay.addRow("Interasse file (m)", self.row_pitch)
        lay.addRow("Distanza tavoli lungo fila (m)", self.table_spacing)
        return group

    def _build_optimization_group(self):
        group = QGroupBox("Sezione D – Ottimizzazione")
        lay = QFormLayout(group)

        self.azimuth_min = self._new_distance(min_val=-180.0, max_val=180.0, default=0.0)
        self.azimuth_max = self._new_distance(min_val=-180.0, max_val=180.0, default=30.0)
        self.azimuth_step = self._new_distance(min_val=0.1, max_val=90.0, default=5.0)

        self.shift_mode_combo = QComboBox()
        self.shift_mode_combo.addItem("Nessuno", self.SHIFT_NONE)
        self.shift_mode_combo.addItem("Alternato", self.SHIFT_ALTERNATED)
        self.shift_mode_combo.addItem("Ottimizzato", self.SHIFT_OPTIMIZED)

        self.shift_value = self._new_distance(default=1.0)
        self.shift_min = self._new_distance(default=0.0)
        self.shift_max = self._new_distance(default=2.0)
        self.shift_step = self._new_distance(min_val=0.1, default=0.5)

        lay.addRow("Azimuth minimo (°)", self.azimuth_min)
        lay.addRow("Azimuth massimo (°)", self.azimuth_max)
        lay.addRow("Passo azimuth (°)", self.azimuth_step)
        lay.addRow("Modalità shift", self.shift_mode_combo)
        lay.addRow("Valore shift (m)", self.shift_value)
        lay.addRow("Shift minimo (m)", self.shift_min)
        lay.addRow("Shift massimo (m)", self.shift_max)
        lay.addRow("Passo shift (m)", self.shift_step)
        return group

    def _build_output_group(self):
        group = QGroupBox("Sezione E – Output")
        lay = QGridLayout(group)

        self.output_dir_edit = QLineEdit()
        self.output_dir_btn = QPushButton("Sfoglia…")
        row0 = QHBoxLayout()
        row0.addWidget(self.output_dir_edit)
        row0.addWidget(self.output_dir_btn)

        self.prefix_edit = QLineEdit("FV")
        self.add_to_project_chk = QCheckBox("Aggiungi layer al progetto")
        self.add_to_project_chk.setChecked(True)

        lay.addWidget(QLabel("Cartella output"), 0, 0)
        lay.addLayout(row0, 0, 1)
        lay.addWidget(QLabel("Prefisso output"), 1, 0)
        lay.addWidget(self.prefix_edit, 1, 1)
        lay.addWidget(self.add_to_project_chk, 2, 1)
        return group

    def _build_execution_group(self):
        group = QGroupBox("Sezione F – Esecuzione")
        lay = QVBoxLayout(group)

        self.run_btn = QPushButton("Genera layout")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(150)

        lay.addWidget(self.run_btn)
        lay.addWidget(self.progress)
        lay.addWidget(self.log_box)
        return group

    def _wire_signals(self):
        self.output_dir_btn.clicked.connect(self._choose_output_dir)
        self.shift_mode_combo.currentIndexChanged.connect(self._toggle_shift_controls)
        self._toggle_shift_controls()

    def _choose_output_dir(self):
        out = QFileDialog.getExistingDirectory(self, "Seleziona cartella output")
        if out:
            self.output_dir_edit.setText(out)

    def _toggle_shift_controls(self):
        mode = self.shift_mode_combo.currentData()
        optimized = mode == self.SHIFT_OPTIMIZED
        self.shift_min.setEnabled(optimized)
        self.shift_max.setEnabled(optimized)
        self.shift_step.setEnabled(optimized)
        self.shift_value.setEnabled(mode == self.SHIFT_ALTERNATED)

    def append_log(self, message: str):
        self.log_box.appendPlainText(message)

    def set_progress(self, value: int):
        self.progress.setValue(int(value))

    def validate(self) -> bool:
        if not self.lots_combo.currentLayer():
            QMessageBox.warning(self, "Input mancante", "Selezionare layer lotti catastali.")
            return False
        if not self.dtm_combo.currentLayer():
            QMessageBox.warning(self, "Input mancante", "Selezionare raster DTM.")
            return False
        if not self.output_dir_edit.text().strip():
            QMessageBox.warning(self, "Output mancante", "Selezionare cartella output.")
            return False
        if self.azimuth_max.value() < self.azimuth_min.value():
            QMessageBox.warning(self, "Azimuth", "Azimuth massimo deve essere >= minimo.")
            return False
        if self.shift_max.value() < self.shift_min.value():
            QMessageBox.warning(self, "Shift", "Shift massimo deve essere >= minimo.")
            return False
        if self.lots_combo.currentLayer().crs().isGeographic():
            QMessageBox.critical(
                self,
                "CRS non metrico",
                "Il layer lotti è in CRS geografico. Usare un CRS proiettato metrico.",
            )
            return False
        return True

    def collect_parameters(self) -> FvLayoutParameters:
        lots = self.lots_combo.currentLayer()
        excluded = self.excluded_combo.currentLayer()
        dtm = self.dtm_combo.currentLayer()
        table_power = self.table_power.value() or (
            self.modules_per_table.value() * self.module_power.value()
        )

        return FvLayoutParameters(
            lots_layer_id=lots.id(),
            excluded_layer_id=excluded.id() if excluded else None,
            dtm_layer_id=dtm.id(),
            selected_only=self.selected_only_chk.isChecked(),
            fence_offset_m=self.fence_offset.value(),
            road_width_m=self.road_width.value(),
            module_road_clearance_m=self.module_clearance.value(),
            slope_limit_deg=self.slope_limit.value(),
            structure_type=self.structure_combo.currentData(),
            table_length_m=self.table_length.value(),
            table_width_m=self.table_width.value(),
            modules_per_table=self.modules_per_table.value(),
            module_power_kw=self.module_power.value(),
            table_power_kw=table_power,
            row_pitch_m=self.row_pitch.value(),
            table_spacing_m=self.table_spacing.value(),
            azimuth_min_deg=self.azimuth_min.value(),
            azimuth_max_deg=self.azimuth_max.value(),
            azimuth_step_deg=self.azimuth_step.value(),
            shift_mode=self.shift_mode_combo.currentData(),
            shift_value_m=self.shift_value.value(),
            shift_min_m=self.shift_min.value(),
            shift_max_m=self.shift_max.value(),
            shift_step_m=self.shift_step.value(),
            output_dir=self.output_dir_edit.text().strip(),
            output_prefix=self.prefix_edit.text().strip() or "FV",
            add_to_project=self.add_to_project_chk.isChecked(),
        )

    def get_layer(self, layer_id):
        return QgsProject.instance().mapLayer(layer_id)
