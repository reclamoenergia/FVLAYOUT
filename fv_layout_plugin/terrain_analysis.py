# -*- coding: utf-8 -*-
import math
import os
from dataclasses import dataclass
from typing import Dict, Optional

import processing
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant

from .geometry_utils import make_valid


@dataclass
class TerrainArtifacts:
    slope_raster_path: str


@dataclass
class SlopeFilterResult:
    lot_id: str
    usable_geom: QgsGeometry
    status: str
    reason: str
    valid_pixels: int
    slope_min: Optional[float]
    slope_max: Optional[float]
    slope_mean: Optional[float]


class TerrainAnalyzer:
    def __init__(self, dtm_layer: QgsRasterLayer, output_dir: str, prefix: str):
        self.dtm = dtm_layer
        self.output_dir = output_dir
        self.prefix = prefix
        self._tmp_dir = os.path.join(output_dir, f"{prefix}_tmp")
        os.makedirs(self._tmp_dir, exist_ok=True)

    def build_slope_raster(self) -> TerrainArtifacts:
        slope_path = os.path.join(self._tmp_dir, f"{self.prefix}_slope_deg.tif")
        processing.run(
            "native:slope",
            {
                "INPUT": self.dtm,
                "Z_FACTOR": 1.0,
                "OUTPUT": slope_path,
            },
        )
        return TerrainArtifacts(slope_raster_path=slope_path)

    def validate_crs_and_overlap(self, lots_layer: QgsVectorLayer) -> Dict[str, str]:
        lot_crs = lots_layer.crs()
        dtm_crs = self.dtm.crs()

        if lot_crs.isGeographic():
            return {
                "ok": False,
                "message": "CRS lotti geografico: usare un CRS proiettato metrico prima dell'analisi.",
            }
        if dtm_crs.isGeographic():
            return {
                "ok": False,
                "message": "CRS DTM geografico: usare un DTM in CRS proiettato metrico.",
            }

        lot_extent = lots_layer.extent()
        dtm_extent = self.dtm.extent()

        if lot_crs != dtm_crs:
            tr = QgsCoordinateTransform(lot_crs, dtm_crs, QgsProject.instance())
            lot_extent = tr.transformBoundingBox(lot_extent)

        overlap = lot_extent.intersect(dtm_extent)
        if overlap.isEmpty() or overlap.width() <= 0 or overlap.height() <= 0:
            return {
                "ok": False,
                "message": "DTM non coerente con i lotti: nessuna sovrapposizione spaziale tra extent.",
            }

        return {
            "ok": True,
            "lots_crs": lot_crs.authid() or lot_crs.description(),
            "dtm_crs": dtm_crs.authid() or dtm_crs.description(),
            "lots_extent": f"{lots_layer.extent().toString()}",
            "dtm_extent": f"{self.dtm.extent().toString()}",
        }

    def _single_feature_layer(self, geom: QgsGeometry, crs: QgsCoordinateReferenceSystem, name: str) -> QgsVectorLayer:
        lyr = QgsVectorLayer(f"Polygon?crs={crs.authid()}", name, "memory")
        prov = lyr.dataProvider()
        prov.addAttributes([QgsField("id", QVariant.Int)])
        lyr.updateFields()
        feat = QgsFeature(QgsFields(lyr.fields()))
        feat.setAttribute("id", 1)
        feat.setGeometry(make_valid(geom))
        prov.addFeature(feat)
        lyr.updateExtents()
        return lyr

    def _collect_raster_stats(self, raster: QgsRasterLayer) -> Dict[str, Optional[float]]:
        provider = raster.dataProvider()
        extent = raster.extent()
        width = raster.width()
        height = raster.height()

        if width <= 0 or height <= 0:
            return {"valid_pixels": 0, "slope_min": None, "slope_max": None, "slope_mean": None}

        block = provider.block(1, extent, width, height)
        if block is None:
            return {"valid_pixels": 0, "slope_min": None, "slope_max": None, "slope_mean": None}

        nodata = provider.sourceNoDataValue(1) if provider.sourceHasNoDataValue(1) else None

        valid = 0
        s_min = None
        s_max = None
        s_sum = 0.0
        for row in range(height):
            for col in range(width):
                value = block.value(row, col)
                if value is None:
                    continue
                try:
                    fval = float(value)
                except Exception:
                    continue
                if math.isnan(fval) or math.isinf(fval):
                    continue
                if nodata is not None and math.isclose(fval, nodata, rel_tol=0.0, abs_tol=1e-12):
                    continue
                valid += 1
                s_sum += fval
                s_min = fval if s_min is None else min(s_min, fval)
                s_max = fval if s_max is None else max(s_max, fval)

        return {
            "valid_pixels": valid,
            "slope_min": s_min,
            "slope_max": s_max,
            "slope_mean": (s_sum / valid) if valid else None,
        }

    def filter_installable_by_slope(
        self,
        lot_id: str,
        lot_geom: QgsGeometry,
        installable_geom: QgsGeometry,
        installable_crs: QgsCoordinateReferenceSystem,
        slope_raster: QgsRasterLayer,
        slope_limit_deg: float,
    ) -> SlopeFilterResult:
        if installable_geom.isEmpty():
            return SlopeFilterResult(lot_id, QgsGeometry(), "technical_error", "geometria installabile vuota", 0, None, None, None)

        work_geom = make_valid(installable_geom)
        to_slope = None
        to_installable = None
        if installable_crs != slope_raster.crs():
            to_slope = QgsCoordinateTransform(installable_crs, slope_raster.crs(), QgsProject.instance())
            to_installable = QgsCoordinateTransform(slope_raster.crs(), installable_crs, QgsProject.instance())
            work_geom.transform(to_slope)

        lot_geom_slope = make_valid(lot_geom)
        if to_slope is not None:
            lot_geom_slope.transform(to_slope)

        if not lot_geom_slope.intersects(QgsGeometry.fromRect(slope_raster.extent())):
            return SlopeFilterResult(
                lot_id,
                QgsGeometry(),
                "technical_error",
                "intersezione vuota tra lotto e raster slope",
                0,
                None,
                None,
                None,
            )

        mask_layer = self._single_feature_layer(work_geom, slope_raster.crs(), f"mask_{lot_id}")
        clipped_path = os.path.join(self._tmp_dir, f"{self.prefix}_lot_{lot_id}_slope_clip.tif")
        allowed_path = os.path.join(self._tmp_dir, f"{self.prefix}_lot_{lot_id}_slope_allowed.tif")
        allowed_gpkg = os.path.join(self._tmp_dir, f"{self.prefix}_lot_{lot_id}_slope_allowed.gpkg")

        processing.run(
            "gdal:cliprasterbymasklayer",
            {
                "INPUT": slope_raster,
                "MASK": mask_layer,
                "SOURCE_CRS": None,
                "TARGET_CRS": None,
                "NODATA": -9999,
                "ALPHA_BAND": False,
                "CROP_TO_CUTLINE": True,
                "KEEP_RESOLUTION": True,
                "SET_RESOLUTION": False,
                "X_RESOLUTION": None,
                "Y_RESOLUTION": None,
                "MULTITHREADING": False,
                "OPTIONS": "",
                "DATA_TYPE": 5,
                "EXTRA": "",
                "OUTPUT": clipped_path,
            },
        )

        clipped_layer = QgsRasterLayer(clipped_path, f"slope_clip_{lot_id}")
        if not clipped_layer.isValid():
            return SlopeFilterResult(lot_id, QgsGeometry(), "technical_error", "campionamento slope fallito", 0, None, None, None)

        stats = self._collect_raster_stats(clipped_layer)
        if stats["valid_pixels"] == 0:
            return SlopeFilterResult(
                lot_id,
                QgsGeometry(),
                "technical_error",
                "nessun pixel slope valido nel lotto (tutti nodata o clipping vuoto)",
                0,
                None,
                None,
                None,
            )

        expr = f"(A <= {float(slope_limit_deg)}) * 1"
        processing.run(
            "gdal:rastercalculator",
            {
                "INPUT_A": clipped_path,
                "BAND_A": 1,
                "FORMULA": expr,
                "RTYPE": 5,
                "NO_DATA": 0,
                "OPTIONS": "",
                "EXTRA": "",
                "OUTPUT": allowed_path,
            },
        )

        processing.run(
            "gdal:polygonize",
            {
                "INPUT": allowed_path,
                "BAND": 1,
                "FIELD": "ok",
                "EIGHT_CONNECTEDNESS": False,
                "EXTRA": "",
                "OUTPUT": f"{allowed_gpkg}|layername=allowed",
            },
        )

        allowed_layer = QgsVectorLayer(f"{allowed_gpkg}|layername=allowed", f"allowed_{lot_id}", "ogr")
        if not allowed_layer.isValid():
            return SlopeFilterResult(
                lot_id,
                QgsGeometry(),
                "technical_error",
                "maschera slope non generata correttamente",
                int(stats["valid_pixels"]),
                stats["slope_min"],
                stats["slope_max"],
                stats["slope_mean"],
            )

        allowed_geom = QgsGeometry()
        first = True
        for feat in allowed_layer.getFeatures():
            try:
                ok_val = int(feat["ok"])
            except Exception:
                ok_val = 0
            if ok_val != 1:
                continue
            g = make_valid(feat.geometry())
            if g.isEmpty():
                continue
            allowed_geom = g if first else make_valid(allowed_geom.combine(g))
            first = False

        if first:
            return SlopeFilterResult(
                lot_id,
                QgsGeometry(),
                "too_steep",
                "tutti i pixel validi superano la soglia di pendenza",
                int(stats["valid_pixels"]),
                stats["slope_min"],
                stats["slope_max"],
                stats["slope_mean"],
            )

        if to_installable is not None:
            allowed_geom.transform(to_installable)

        usable = make_valid(installable_geom.intersection(allowed_geom))
        if usable.isEmpty():
            return SlopeFilterResult(
                lot_id,
                QgsGeometry(),
                "too_steep",
                "nessuna area ammessa dopo filtro pendenza",
                int(stats["valid_pixels"]),
                stats["slope_min"],
                stats["slope_max"],
                stats["slope_mean"],
            )

        return SlopeFilterResult(
            lot_id,
            usable,
            "ok",
            "",
            int(stats["valid_pixels"]),
            stats["slope_min"],
            stats["slope_max"],
            stats["slope_mean"],
        )

    def sample_table_terrain(self, table_geom: QgsGeometry, slope_raster: QgsRasterLayer, dtm_raster: QgsRasterLayer):
        bb = table_geom.boundingBox()
        points = [
            QgsPointXY(bb.xMinimum(), bb.yMinimum()),
            QgsPointXY(bb.xMinimum(), bb.yMaximum()),
            QgsPointXY(bb.xMaximum(), bb.yMinimum()),
            QgsPointXY(bb.xMaximum(), bb.yMaximum()),
            table_geom.centroid().asPoint(),
        ]

        z_vals, slope_vals = [], []
        for pt in points:
            z = dtm_raster.dataProvider().sample(pt, 1)
            s = slope_raster.dataProvider().sample(pt, 1)
            if z[1] and z[0] is not None:
                z_vals.append(float(z[0]))
            if s[1] and s[0] is not None:
                slope_vals.append(float(s[0]))

        if not z_vals:
            return {"z_mean": None, "z_min": None, "z_max": None, "slope_mean": None}
        return {
            "z_mean": sum(z_vals) / len(z_vals),
            "z_min": min(z_vals),
            "z_max": max(z_vals),
            "slope_mean": (sum(slope_vals) / len(slope_vals)) if slope_vals else None,
        }
