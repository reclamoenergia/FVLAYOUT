# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import List, Optional, Tuple

from qgis.core import QgsGeometry, QgsPointXY, QgsRectangle


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


@dataclass
class _RowCandidate:
    shift_m: float
    tables: List[QgsGeometry]
    score_tables: int
    occupied_span_m: float


class LayoutEngine:
    def __init__(self, params):
        self.params = params

    def build_for_area(
        self,
        area_geom: QgsGeometry,
        azimuth_deg: float,
        excluded_geom: Optional[QgsGeometry] = None,
    ):
        centroid = area_geom.centroid().asPoint()
        rotated_area = QgsGeometry(area_geom)
        rotated_area.rotate(-azimuth_deg, centroid)
        rotated_excluded = self._rotate_optional_geometry(excluded_geom, -azimuth_deg, centroid)
        bb = rotated_area.boundingBox()

        table_len = self.params.table_length_m
        table_wid = self.params.table_width_m

        if self.params.row_pitch_m < table_wid or self.params.table_spacing_m < 0:
            return [], []
        if bb.width() < table_len or bb.height() < table_wid:
            return [], []

        rows: List[RowResult] = []
        tables: List[TableResult] = []

        row_index = 0
        y = bb.yMinimum() + table_wid / 2.0
        while y <= bb.yMaximum() - table_wid / 2.0 + 1e-9:
            row_index += 1
            best_row = self._build_best_row_for_axis(
                row_id=row_index,
                y=y,
                bb=bb,
                usable_geom=rotated_area,
                excluded_geom=rotated_excluded,
                azimuth_deg=azimuth_deg,
            )
            if best_row is not None:
                row_result, row_tables = best_row
                rows.append(row_result)
                tables.extend(row_tables)
            y += self.params.row_pitch_m

        for r in rows:
            r.geom.rotate(azimuth_deg, centroid)
        for t in tables:
            t.geom.rotate(azimuth_deg, centroid)

        return rows, tables

    def _row_shift_candidates(self, row_id: int) -> List[float]:
        mode = self.params.shift_mode
        if mode == "none":
            return [0.0]
        if mode == "alternated":
            return [0.0 if (row_id % 2 == 0) else self.params.shift_value_m]

        start = self.params.shift_min_m
        stop = self.params.shift_max_m
        step = self.params.shift_step_m
        if step <= 0:
            return [start]

        vals: List[float] = []
        x = start
        while x <= stop + 1e-9:
            vals.append(round(x, 8))
            x += step
        return vals if vals else [0.0]

    def _generate_tables_on_row(self, y: float, bb: QgsRectangle, shift_m: float) -> List[QgsGeometry]:
        table_len = self.params.table_length_m
        table_wid = self.params.table_width_m
        step_x = table_len + self.params.table_spacing_m

        x = bb.xMinimum() + table_len / 2.0 + shift_m
        tables: List[QgsGeometry] = []
        while x <= bb.xMaximum() - table_len / 2.0 + 1e-9:
            rect = QgsRectangle(
                x - table_len / 2.0,
                y - table_wid / 2.0,
                x + table_len / 2.0,
                y + table_wid / 2.0,
            )
            tables.append(QgsGeometry.fromRect(rect))
            x += step_x
        return tables

    def _evaluate_row_tables(
        self,
        tables: List[QgsGeometry],
        usable_geom: QgsGeometry,
        excluded_geom: Optional[QgsGeometry],
    ) -> List[QgsGeometry]:
        valid: List[QgsGeometry] = []
        has_exclusions = excluded_geom is not None and not excluded_geom.isEmpty()
        for tb in tables:
            if not usable_geom.contains(tb):
                continue
            if has_exclusions and tb.intersects(excluded_geom):
                continue
            valid.append(tb)
        return valid

    def _build_best_row_for_axis(
        self,
        row_id: int,
        y: float,
        bb: QgsRectangle,
        usable_geom: QgsGeometry,
        excluded_geom: Optional[QgsGeometry],
        azimuth_deg: float,
    ) -> Optional[Tuple[RowResult, List[TableResult]]]:
        best: Optional[_RowCandidate] = None
        for shift_m in self._row_shift_candidates(row_id):
            candidate_tables = self._generate_tables_on_row(y, bb, shift_m)
            valid_tables = self._evaluate_row_tables(candidate_tables, usable_geom, excluded_geom)
            if not valid_tables:
                cur = _RowCandidate(shift_m=shift_m, tables=[], score_tables=0, occupied_span_m=0.0)
            else:
                x_min = min(tb.boundingBox().xMinimum() for tb in valid_tables)
                x_max = max(tb.boundingBox().xMaximum() for tb in valid_tables)
                cur = _RowCandidate(
                    shift_m=shift_m,
                    tables=valid_tables,
                    score_tables=len(valid_tables),
                    occupied_span_m=max(0.0, x_max - x_min),
                )
            if self._is_better_row_candidate(cur, best):
                best = cur

        if best is None or best.score_tables <= 0:
            return None

        row_x_min = min(tb.boundingBox().xMinimum() for tb in best.tables)
        row_x_max = max(tb.boundingBox().xMaximum() for tb in best.tables)
        axis_geom = QgsGeometry.fromPolylineXY([QgsPointXY(row_x_min, y), QgsPointXY(row_x_max, y)])
        row_result = RowResult(
            row_id=row_id,
            azimuth=azimuth_deg,
            shift_m=best.shift_m,
            geom=axis_geom,
        )

        row_tables: List[TableResult] = []
        for tidx, tgeom in enumerate(best.tables, start=1):
            row_tables.append(
                TableResult(
                    row_id=row_id,
                    table_id=tidx,
                    azimuth=azimuth_deg,
                    shift_m=best.shift_m,
                    geom=tgeom,
                )
            )
        return row_result, row_tables

    def _is_better_row_candidate(self, cur: _RowCandidate, best: Optional[_RowCandidate]) -> bool:
        if best is None:
            return True
        if cur.score_tables != best.score_tables:
            return cur.score_tables > best.score_tables
        if abs(cur.occupied_span_m - best.occupied_span_m) > 1e-9:
            return cur.occupied_span_m > best.occupied_span_m
        return abs(cur.shift_m) < abs(best.shift_m)

    def _rotate_optional_geometry(self, geom: Optional[QgsGeometry], angle_deg: float, pivot) -> Optional[QgsGeometry]:
        if geom is None:
            return None
        if geom.isEmpty():
            return QgsGeometry(geom)
        out = QgsGeometry(geom)
        out.rotate(angle_deg, pivot)
        return out

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
