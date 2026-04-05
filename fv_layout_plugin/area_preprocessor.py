# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import Dict, List

from qgis.core import QgsFeatureRequest, QgsGeometry

from .geometry_utils import dissolve_features, make_valid, polygon_parts, safe_buffer


@dataclass
class LotPrepared:
    lot_id: str
    lot_geom: QgsGeometry
    domain_geom: QgsGeometry
    fence_line: QgsGeometry
    road_geom: QgsGeometry
    installable_geom: QgsGeometry
    area_catastale_m2: float
    area_esclusa_m2: float
    area_recinzione_interna_m2: float
    area_viabilita_m2: float
    area_netta_installabile_m2: float
    log_messages: List[str]


def collect_lot_features(lots_layer, selected_only: bool):
    feats = lots_layer.selectedFeatures() if selected_only else list(lots_layer.getFeatures())
    return feats


def build_exclusions_geom(excluded_layer) -> QgsGeometry:
    if not excluded_layer:
        return QgsGeometry()
    return dissolve_features(excluded_layer.getFeatures())


def _offset_inside(geom: QgsGeometry, distance_m: float) -> QgsGeometry:
    return safe_buffer(geom, -abs(distance_m))


def prepare_lots(lots_layer, excluded_geom: QgsGeometry, params) -> List[LotPrepared]:
    outputs = []
    for idx, feat in enumerate(collect_lot_features(lots_layer, params.selected_only), start=1):
        log = []
        lot_id = str(feat["id"]) if "id" in feat.fields().names() else str(feat.id())
        lot_geom = make_valid(feat.geometry())
        if lot_geom.isEmpty():
            log.append("Geometria lotto vuota o invalida: saltato")
            continue

        area_cat = lot_geom.area()
        domain_geom = lot_geom

        if not excluded_geom.isEmpty():
            domain_geom = make_valid(domain_geom.difference(excluded_geom))
        area_excluded = max(0.0, area_cat - domain_geom.area())
        if domain_geom.isEmpty():
            log.append("Lotto interamente escluso")
            continue

        fence_polygon = _offset_inside(domain_geom, params.fence_offset_m)
        if fence_polygon.isEmpty():
            log.append("Offset recinzione collassato")
            continue

        inner_road_polygon = _offset_inside(
            fence_polygon, params.road_width_m
        )
        if inner_road_polygon.isEmpty():
            log.append("Offset viabilità collassato")
            continue

        road_geom = make_valid(fence_polygon.difference(inner_road_polygon))

        installable_geom = _offset_inside(inner_road_polygon, params.module_road_clearance_m)
        if installable_geom.isEmpty():
            log.append("Area installabile nulla dopo margine moduli")
            continue

        min_table_area = params.table_length_m * params.table_width_m
        kept_parts = [p for p in polygon_parts(installable_geom) if p.area() >= min_table_area]
        if not kept_parts:
            log.append("Nessuna area sufficiente per almeno un tavolo")
            continue
        merged_installable = kept_parts[0]
        for piece in kept_parts[1:]:
            merged_installable = make_valid(merged_installable.combine(piece))

        fence_line = make_valid(fence_polygon.boundary())

        outputs.append(
            LotPrepared(
                lot_id=lot_id,
                lot_geom=lot_geom,
                domain_geom=domain_geom,
                fence_line=fence_line,
                road_geom=road_geom,
                installable_geom=merged_installable,
                area_catastale_m2=area_cat,
                area_esclusa_m2=area_excluded,
                area_recinzione_interna_m2=fence_polygon.area(),
                area_viabilita_m2=road_geom.area(),
                area_netta_installabile_m2=merged_installable.area(),
                log_messages=log,
            )
        )

    return outputs
