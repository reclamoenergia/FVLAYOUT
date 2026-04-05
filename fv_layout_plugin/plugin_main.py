# -*- coding: utf-8 -*-
import os
import traceback

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.core import QgsProject, QgsRasterLayer

from .area_preprocessor import build_exclusions_geom, prepare_lots
from .layout_dialog import LayoutDialog
from .optimizer import LayoutOptimizer
from .output_writer import OutputWriter
from .terrain_analysis import TerrainAnalyzer


class FvLayoutPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dlg = None

    def initGui(self):
        self.action = QAction("FV Layout Auto Designer", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu("&FV Layout", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action:
            self.iface.removePluginMenu("&FV Layout", self.action)
            self.iface.removeToolBarIcon(self.action)

    def run(self):
        self.dlg = LayoutDialog(self.iface.mainWindow())
        self.dlg.run_btn.clicked.connect(self._on_run_clicked)
        self.dlg.show()

    def _on_run_clicked(self):
        if not self.dlg.validate():
            return

        params = self.dlg.collect_parameters()
        lots_layer = self.dlg.get_layer(params.lots_layer_id)
        excluded_layer = self.dlg.get_layer(params.excluded_layer_id) if params.excluded_layer_id else None
        dtm_layer = self.dlg.get_layer(params.dtm_layer_id)

        try:
            self.dlg.set_progress(5)
            self.dlg.append_log("Preparazione geometrie lotti…")
            excluded_geom = build_exclusions_geom(excluded_layer)
            prepared = prepare_lots(lots_layer, excluded_geom, params)
            if not prepared:
                QMessageBox.warning(self.dlg, "Nessun lotto valido", "Nessuna geometria elaborabile.")
                return

            self.dlg.set_progress(15)
            self.dlg.append_log("Analisi pendenza da DTM…")
            terrain = TerrainAnalyzer(dtm_layer, params.output_dir, params.output_prefix)
            slope_mask, artifacts = terrain.build_slope_mask_polygon(params.slope_limit_deg)
            slope_raster = QgsRasterLayer(artifacts.slope_raster_path, "slope")

            optimizer = LayoutOptimizer(params)
            results = []

            for i, lot in enumerate(prepared, start=1):
                self.dlg.append_log(f"Lotto {lot.lot_id}: filtro aree con pendenza > soglia")
                usable = terrain.filter_installable_by_slope(
                    lot.installable_geom,
                    lots_layer.crs(),
                    slope_mask,
                )
                if usable.isEmpty():
                    self.dlg.append_log(f"Lotto {lot.lot_id}: nessuna area ammessa dopo filtro pendenza")
                    continue

                self.dlg.append_log(f"Lotto {lot.lot_id}: ottimizzazione azimuth/shift")
                solution = optimizer.solve(usable, excluded_geom)

                table_payload = []
                for tb in solution.tables:
                    terr = terrain.sample_table_terrain(tb.geom, slope_raster, dtm_layer)
                    table_payload.append(
                        {
                            "row_id": tb.row_id,
                            "table_id": tb.table_id,
                            "azimuth": tb.azimuth,
                            "shift_m": tb.shift_m,
                            "geom": tb.geom,
                            "terrain": terr,
                        }
                    )

                results.append(
                    {
                        "lot_id": lot.lot_id,
                        "fence_line": lot.fence_line,
                        "road_geom": lot.road_geom,
                        "usable_geom": usable,
                        "rows": solution.rows,
                        "tables": table_payload,
                        "azimuth": solution.azimuth,
                        "shift": solution.shift,
                        "stats": {
                            "area_catastale_m2": lot.area_catastale_m2,
                            "area_esclusa_m2": lot.area_esclusa_m2,
                            "area_recinzione_interna_m2": lot.area_recinzione_interna_m2,
                            "area_viabilita_m2": lot.area_viabilita_m2,
                        },
                    }
                )
                self.dlg.set_progress(20 + int(65 * i / len(prepared)))

            if not results:
                QMessageBox.warning(self.dlg, "Nessun risultato", "Nessun lotto ha prodotto tavoli validi.")
                return

            writer = OutputWriter(params, lots_layer.crs().authid())
            out_files = writer.write(results)
            self.dlg.set_progress(100)
            self.dlg.append_log(f"Output GPKG: {out_files['gpkg']}")
            self.dlg.append_log(f"Report CSV: {out_files['report']}")
            QMessageBox.information(self.dlg, "Completato", "Layout generato con successo.")
        except Exception as exc:
            self.dlg.append_log(f"Errore: {exc}")
            self.dlg.append_log(traceback.format_exc())
            QMessageBox.critical(self.dlg, "Errore", str(exc))
