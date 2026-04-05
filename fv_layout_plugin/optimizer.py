# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import List

from qgis.core import QgsGeometry

from .layout_engine import LayoutEngine, RowResult, TableResult


@dataclass
class LayoutSolution:
    azimuth: float
    shift: float
    rows: List[RowResult]
    tables: List[TableResult]
    score_tables: int
    residual_fragments: int


class LayoutOptimizer:
    def __init__(self, params):
        self.params = params
        self.engine = LayoutEngine(params)

    def _frange(self, start, stop, step):
        vals = []
        x = start
        if step <= 0:
            return [start]
        while x <= stop + 1e-9:
            vals.append(round(x, 8))
            x += step
        return vals

    def _shift_candidates(self):
        mode = self.params.shift_mode
        if mode == "none":
            return [0.0]
        if mode == "alternated":
            return [self.params.shift_value_m]
        return self._frange(
            self.params.shift_min_m,
            self.params.shift_max_m,
            self.params.shift_step_m,
        )

    def solve(self, usable_geom: QgsGeometry, excluded_geom: QgsGeometry) -> LayoutSolution:
        az_values = self._frange(
            self.params.azimuth_min_deg,
            self.params.azimuth_max_deg,
            self.params.azimuth_step_deg,
        )
        best = None
        for az in az_values:
            for sh in self._shift_candidates():
                rows, tables = self.engine.build_for_area(usable_geom, az, sh)
                tables = self.engine.filter_tables_by_containment_and_exclusion(
                    tables, usable_geom, excluded_geom
                )
                if not tables:
                    continue
                residual = usable_geom
                for t in tables:
                    residual = residual.difference(t.geom)
                fragments = len(residual.asGeometryCollection()) if not residual.isEmpty() else 0
                cur = LayoutSolution(
                    azimuth=az,
                    shift=sh,
                    rows=rows,
                    tables=tables,
                    score_tables=len(tables),
                    residual_fragments=fragments,
                )
                if self._is_better(cur, best):
                    best = cur

        if best is None:
            best = LayoutSolution(azimuth=az_values[0], shift=0.0, rows=[], tables=[], score_tables=0, residual_fragments=999999)
        return best

    def _is_better(self, cur: LayoutSolution, best: LayoutSolution):
        if best is None:
            return True
        if cur.score_tables != best.score_tables:
            return cur.score_tables > best.score_tables
        return cur.residual_fragments < best.residual_fragments
