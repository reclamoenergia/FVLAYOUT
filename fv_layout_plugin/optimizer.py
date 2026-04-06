# -*- coding: utf-8 -*-
from dataclasses import dataclass
from math import ceil
from typing import Callable, List, Optional

from qgis.core import QgsGeometry
from qgis.PyQt.QtCore import QCoreApplication

from .layout_engine import LayoutEngine, RowResult, TableResult


@dataclass
class LayoutSolution:
    azimuth: float
    shift: float
    rows: List[RowResult]
    tables: List[TableResult]
    score_tables: int
    score_area_m2: float
    row_count: int


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

    def _downsample_values(self, values: List[float], stride: int) -> List[float]:
        if stride <= 1 or len(values) <= 1:
            return values
        sampled = values[::stride]
        if sampled[-1] != values[-1]:
            sampled.append(values[-1])
        return sampled

    def solve(
        self,
        usable_geom: QgsGeometry,
        excluded_geom: QgsGeometry,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        log_cb: Optional[Callable[[str], None]] = None,
    ) -> LayoutSolution:
        az_values_full = self._frange(
            self.params.azimuth_min_deg,
            self.params.azimuth_max_deg,
            self.params.azimuth_step_deg,
        )
        shift_values_full = self._shift_candidates()
        max_combinations = 1000
        total_full = len(az_values_full) * len(shift_values_full)
        stride = 1
        if total_full > max_combinations:
            stride = max(2, ceil(total_full / float(max_combinations)))
            if log_cb:
                log_cb(
                    f"Ottimizzazione: {total_full} combinazioni richieste; "
                    f"applico campionamento automatico (stride={stride})."
                )
        az_values = self._downsample_values(az_values_full, stride)
        shift_values = self._downsample_values(shift_values_full, stride)
        total = len(az_values) * len(shift_values)
        if log_cb:
            log_cb(f"Ottimizzazione: combinazioni da valutare = {total}")

        best = None
        idx = 0
        for az in az_values:
            for sh in shift_values:
                idx += 1
                if progress_cb:
                    progress_cb(idx, total)
                if log_cb and (idx == 1 or idx == total or (idx % 10) == 0):
                    log_cb(f"Ottimizzazione: combinazione {idx}/{total} (az={az:.2f}, shift={sh:.2f})")

                rows, tables = self.engine.build_for_area(usable_geom, az, sh)
                tables = self.engine.filter_tables_by_containment_and_exclusion(
                    tables, usable_geom, excluded_geom
                )
                if not tables:
                    if idx % 10 == 0:
                        QCoreApplication.processEvents()
                    continue
                score_area = sum(t.geom.area() for t in tables)
                cur = LayoutSolution(
                    azimuth=az,
                    shift=sh,
                    rows=rows,
                    tables=tables,
                    score_tables=len(tables),
                    score_area_m2=score_area,
                    row_count=len(rows),
                )
                if self._is_better(cur, best):
                    best = cur
                    if log_cb:
                        log_cb(
                            f"Nuova soluzione migliore: {cur.score_tables} tavoli "
                            f"(az={cur.azimuth:.2f}, shift={cur.shift:.2f})"
                        )
                if idx % 10 == 0:
                    QCoreApplication.processEvents()

        if best is None:
            fallback_az = az_values[0] if az_values else self.params.azimuth_min_deg
            best = LayoutSolution(
                azimuth=fallback_az,
                shift=0.0,
                rows=[],
                tables=[],
                score_tables=0,
                score_area_m2=0.0,
                row_count=0,
            )
        return best

    def _is_better(self, cur: LayoutSolution, best: LayoutSolution):
        if best is None:
            return True
        if cur.score_tables != best.score_tables:
            return cur.score_tables > best.score_tables
        if abs(cur.score_area_m2 - best.score_area_m2) > 1e-9:
            return cur.score_area_m2 > best.score_area_m2
        return cur.row_count < best.row_count
