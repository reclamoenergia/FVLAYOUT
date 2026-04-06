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

            crs_check = terrain.validate_crs_and_overlap(lots_layer)
            if not crs_check.get("ok"):
                QMessageBox.critical(self.dlg, "Errore CRS/overlap", crs_check.get("message", "Errore di coerenza CRS"))
                self.dlg.append_log(crs_check.get("message", "Errore di coerenza CRS"))
                return

            self.dlg.append_log(f"CRS lotti: {crs_check['lots_crs']}")
            self.dlg.append_log(f"CRS DTM: {crs_check['dtm_crs']}")
            self.dlg.append_log(f"Extent lotti: {crs_check['lots_extent']}")
            self.dlg.append_log(f"Extent DTM: {crs_check['dtm_extent']}")
            self.dlg.append_log(f"Soglia pendenza configurata: {params.slope_limit_deg:.2f} gradi")

            artifacts = terrain.build_slope_raster()
            slope_raster = QgsRasterLayer(artifacts.slope_raster_path, "slope_deg")
            if not slope_raster.isValid():
                raise RuntimeError("Raster slope non valido: generazione da DTM fallita")

            optimizer = LayoutOptimizer(params)
            results = []

            for i, lot in enumerate(prepared, start=1):
                self.dlg.append_log(f"Lotto {lot.lot_id}: filtro pendenza avviato")
                self.dlg.append_log(f"Lotto {lot.lot_id}: area lotto iniziale = {lot.area_catastale_m2:.1f} m2")
                self.dlg.append_log(f"Lotto {lot.lot_id}: area utile prima filtro = {lot.installable_geom.area():.1f} m2")
                self.dlg.append_log(f"Lotto {lot.lot_id}: soglia = {params.slope_limit_deg:.1f} gradi")
                self.dlg.append_log(f"Lotto {lot.lot_id}: unità pendenza = gradi")

                slope_result = terrain.filter_installable_by_slope(
                    lot_id=lot.lot_id,
                    lot_geom=lot.lot_geom,
                    installable_geom=lot.installable_geom,
                    installable_crs=lots_layer.crs(),
                    slope_raster=slope_raster,
                    slope_limit_deg=params.slope_limit_deg,
                )

                if slope_result.valid_pixels > 0:
                    self.dlg.append_log(f"Lotto {lot.lot_id}: pixel validi slope = {slope_result.valid_pixels}")
                    self.dlg.append_log(
                        f"Lotto {lot.lot_id}: slope min/max/mean = "
                        f"{slope_result.slope_min:.2f} / {slope_result.slope_max:.2f} / {slope_result.slope_mean:.2f}"
                    )
                else:
                    self.dlg.append_log(f"Lotto {lot.lot_id}: nessun pixel slope valido nel lotto")

                if slope_result.status == "technical_error":
                    self.dlg.append_log(f"Lotto {lot.lot_id}: lotto saltato per errore tecnico analisi raster ({slope_result.reason})")
                    continue

                usable = slope_result.usable_geom
                if usable.isEmpty():
                    self.dlg.append_log(f"Lotto {lot.lot_id}: escluso per pendenza reale ({slope_result.reason})")
                    continue

                self.dlg.append_log(f"Lotto {lot.lot_id}: area utile dopo filtro = {usable.area():.1f} m2")

                self.dlg.append_log(f"Lotto {lot.lot_id}: ottimizzazione azimuth/shift")
                base_progress = 20 + int(65 * (i - 1) / len(prepared))
                next_progress = 20 + int(65 * i / len(prepared))

                def _opt_progress(done, total):
                    if total <= 0:
                        return
                    frac = done / float(total)
                    self.dlg.set_progress(base_progress + int((next_progress - base_progress) * frac))

                def _opt_log(msg):
                    self.dlg.append_log(f"Lotto {lot.lot_id}: {msg}")

                solution = optimizer.solve(usable, excluded_geom, progress_cb=_opt_progress, log_cb=_opt_log)

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
