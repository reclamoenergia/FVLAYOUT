# -*- coding: utf-8 -*-
import os
from dataclasses import dataclass
from typing import Dict, Tuple

import processing
from qgis.core import (
    QgsCoordinateTransform,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)

from .geometry_utils import make_valid


@dataclass
class TerrainArtifacts:
    slope_raster_path: str
    mask_raster_path: str
    mask_vector_path: str


class TerrainAnalyzer:
    def __init__(self, dtm_layer: QgsRasterLayer, output_dir: str, prefix: str):
        self.dtm = dtm_layer
        self.output_dir = output_dir
        self.prefix = prefix
        self._tmp_dir = os.path.join(output_dir, f"{prefix}_tmp")
        os.makedirs(self._tmp_dir, exist_ok=True)

    def build_slope_mask_polygon(self, slope_limit_deg: float) -> Tuple[QgsVectorLayer, TerrainArtifacts]:
        slope_path = os.path.join(self._tmp_dir, f"{self.prefix}_slope.tif")
        mask_path = os.path.join(self._tmp_dir, f"{self.prefix}_slope_ok.tif")
        mask_vec_path = os.path.join(self._tmp_dir, f"{self.prefix}_slope_ok.gpkg")

        processing.run(
            "native:slope",
            {
                "INPUT": self.dtm,
                "Z_FACTOR": 1.0,
                "OUTPUT": slope_path,
            },
        )

        expr = f"(A <= {float(slope_limit_deg)}) * 1"
        processing.run(
            "gdal:rastercalculator",
            {
                "INPUT_A": slope_path,
                "BAND_A": 1,
                "FORMULA": expr,
                "RTYPE": 5,
                "NO_DATA": 0,
                "OPTIONS": "",
                "EXTRA": "",
                "OUTPUT": mask_path,
            },
        )

        processing.run(
            "gdal:polygonize",
            {
                "INPUT": mask_path,
                "BAND": 1,
                "FIELD": "ok",
                "EIGHT_CONNECTEDNESS": False,
                "EXTRA": "",
                "OUTPUT": f"{mask_vec_path}|layername=slope_ok",
            },
        )

        mask_layer = QgsVectorLayer(f"{mask_vec_path}|layername=slope_ok", "slope_ok", "ogr")
        artifacts = TerrainArtifacts(
            slope_raster_path=slope_path,
            mask_raster_path=mask_path,
            mask_vector_path=mask_vec_path,
        )
        return mask_layer, artifacts

    def filter_installable_by_slope(
        self,
        installable_geom: QgsGeometry,
        installable_crs,
        slope_mask_layer: QgsVectorLayer,
    ):
        slope_to_installable = None
        if installable_crs != slope_mask_layer.crs():
            slope_to_installable = QgsCoordinateTransform(
                slope_mask_layer.crs(),
                installable_crs,
                QgsProject.instance(),
            )

        allowed = QgsGeometry()
        first = True
        for feat in slope_mask_layer.getFeatures():
            if feat["ok"] != 1:
                continue
            g = make_valid(feat.geometry())
            if g.isEmpty():
                continue
            if slope_to_installable is not None:
                g.transform(slope_to_installable)
            allowed = g if first else make_valid(allowed.combine(g))
            first = False
        if first:
            return QgsGeometry()
        return make_valid(installable_geom.intersection(allowed))

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
