# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import Dict, List, Tuple

from qgis.core import QgsGeometry, QgsPointXY, QgsRectangle

from .geometry_utils import make_valid, polygon_parts


@dataclass
class RowResult:
    row_id: int
    azimuth: float
    shift_m: float
    geom: QgsGeometry


@dataclass
class TableResult:
    row_id: int
    table_id: int
    azimuth: float
    shift_m: float
    geom: QgsGeometry


class LayoutEngine:
    def __init__(self, params):
        self.params = params

    def build_for_area(self, area_geom: QgsGeometry, azimuth_deg: float, shift_m: float):
        centroid = area_geom.centroid().asPoint()
        rotated_area = QgsGeometry(area_geom)
        rotated_area.rotate(-azimuth_deg, centroid)
        bb = rotated_area.boundingBox()

        row_pitch = self.params.row_pitch_m
        table_len = self.params.table_length_m
        table_wid = self.params.table_width_m
        table_gap = self.params.table_spacing_m

        rows: List[RowResult] = []
        tables: List[TableResult] = []
        occupied: List[QgsGeometry] = []

        row_index = 0
        y = bb.yMinimum() + table_wid / 2.0
        while y <= bb.yMaximum() - table_wid / 2.0:
            row_index += 1
            local_shift = shift_m if (row_index % 2 == 0) else 0.0
            x = bb.xMinimum() + table_len / 2.0 + local_shift

            row_tables = []
            while x <= bb.xMaximum() - table_len / 2.0:
                rect = QgsRectangle(
                    x - table_len / 2.0,
                    y - table_wid / 2.0,
                    x + table_len / 2.0,
                    y + table_wid / 2.0,
                )
                table_geom = QgsGeometry.fromRect(rect)
                if rotated_area.contains(table_geom):
                    overlap = any(table_geom.intersects(o) for o in occupied)
                    if not overlap:
                        row_tables.append(table_geom)
                        occupied.append(table_geom)
                x += table_len + table_gap

            if row_tables:
                row_union = row_tables[0]
                for tt in row_tables[1:]:
                    row_union = row_union.combine(tt)
                row_line = row_union.boundingBox()
                axis = QgsGeometry.fromPolylineXY(
                    [
                        QgsPointXY(row_line.xMinimum(), y),
                        QgsPointXY(row_line.xMaximum(), y),
                    ]
                )
                rows.append(RowResult(row_id=row_index, azimuth=azimuth_deg, shift_m=local_shift, geom=axis))
                for tidx, tgeom in enumerate(row_tables, start=1):
                    tables.append(
                        TableResult(
                            row_id=row_index,
                            table_id=tidx,
                            azimuth=azimuth_deg,
                            shift_m=local_shift,
                            geom=tgeom,
                        )
                    )
            y += row_pitch

        # Rotate all geometries back
        for r in rows:
            r.geom.rotate(azimuth_deg, centroid)
        for t in tables:
            t.geom.rotate(azimuth_deg, centroid)

        return rows, tables

    def filter_tables_by_containment_and_exclusion(
        self,
        tables: List[TableResult],
        usable_geom: QgsGeometry,
        excluded_geom: QgsGeometry,
    ) -> List[TableResult]:
        valid = []
        for tb in tables:
            if not usable_geom.contains(tb.geom):
                continue
            if not excluded_geom.isEmpty() and tb.geom.intersects(excluded_geom):
                continue
            valid.append(tb)
        return valid
