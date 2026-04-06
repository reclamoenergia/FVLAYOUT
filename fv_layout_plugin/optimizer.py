# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import Callable, List, Optional

from qgis.core import QgsGeometry
from qgis.PyQt.QtCore import QCoreApplication

from .layout_engine import LayoutEngine, RowResult, TableResult


@dataclass
class LayoutSolution:
    azimuth: float
    rows: List[RowResult]
    tables: List[TableResult]
    score_tables: int
    score_area_m2: float
    row_count: int
    shift_min_m: float
    shift_max_m: float
    shift_mean_m: float


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

    def _shift_stats(self, rows: List[RowResult]):
        if not rows:
            return 0.0, 0.0, 0.0
        shifts = [r.shift_m for r in rows]
        return min(shifts), max(shifts), sum(shifts) / float(len(shifts))

    def solve(
        self,
        usable_geom: QgsGeometry,
        excluded_geom: QgsGeometry,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        log_cb: Optional[Callable[[str], None]] = None,
    ) -> LayoutSolution:
        az_values = self._frange(
            self.params.azimuth_min_deg,
            self.params.azimuth_max_deg,
            self.params.azimuth_step_deg,
        )
        total = len(az_values)
        if log_cb:
            log_cb("ottimizzazione azimuth")
            log_cb(f"azimuth candidati = {total}")

        best = None
        for idx, az in enumerate(az_values, start=1):
            if progress_cb:
                progress_cb(idx, total)
            if log_cb:
                log_cb(f"test azimuth {idx}/{total} (az={az:.2f})")

            rows, tables = self.engine.build_for_area(
                usable_geom,
                az,
                excluded_geom=excluded_geom,
            )
            if not tables:
                if log_cb:
                    log_cb(f"az={az:.2f} -> 0 tavoli")
                if idx % 5 == 0:
                    QCoreApplication.processEvents()
                continue

            score_area = sum(t.geom.area() for t in tables)
            shift_min_m, shift_max_m, shift_mean_m = self._shift_stats(rows)
            cur = LayoutSolution(
                azimuth=az,
                rows=rows,
                tables=tables,
                score_tables=len(tables),
                score_area_m2=score_area,
                row_count=len(rows),
                shift_min_m=shift_min_m,
                shift_max_m=shift_max_m,
                shift_mean_m=shift_mean_m,
            )
            if log_cb:
                log_cb(f"az={az:.2f} -> {cur.score_tables} tavoli")

            if self._is_better(cur, best):
                best = cur
                if log_cb:
                    log_cb(
                        f"nuova soluzione migliore: {cur.score_tables} tavoli "
                        f"(az={cur.azimuth:.2f})"
                    )
            if idx % 5 == 0:
                QCoreApplication.processEvents()

        if best is None:
            fallback_az = az_values[0] if az_values else self.params.azimuth_min_deg
            best = LayoutSolution(
                azimuth=fallback_az,
                rows=[],
                tables=[],
                score_tables=0,
                score_area_m2=0.0,
                row_count=0,
                shift_min_m=0.0,
                shift_max_m=0.0,
                shift_mean_m=0.0,
            )
        return best

    def _is_better(self, cur: LayoutSolution, best: Optional[LayoutSolution]):
        if best is None:
            return True
        if cur.score_tables != best.score_tables:
            return cur.score_tables > best.score_tables
        if abs(cur.score_area_m2 - best.score_area_m2) > 1e-9:
            return cur.score_area_m2 > best.score_area_m2
        return cur.row_count < best.row_count
