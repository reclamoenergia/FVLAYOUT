# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import Dict, List

from qgis.core import QgsFeatureRequest, QgsGeometry, QgsWkbTypes

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


def polygon_outer_boundary_as_line(geom: QgsGeometry) -> QgsGeometry:
    if geom is None or geom.isEmpty():
        return QgsGeometry()

    try:
        valid_geom = make_valid(geom)
    except Exception:
        return QgsGeometry()

    if valid_geom.isEmpty():
        return QgsGeometry()

    if QgsWkbTypes.geometryType(valid_geom.wkbType()) != QgsWkbTypes.PolygonGeometry:
        return QgsGeometry()

    outer_rings = []
    if valid_geom.isMultipart():
        try:
            polygons = valid_geom.asMultiPolygon()
        except Exception:
            return QgsGeometry()
        for poly in polygons:
            if not poly or not poly[0]:
                continue
            outer_rings.append(poly[0])
    else:
        try:
            polygon = valid_geom.asPolygon()
        except Exception:
            return QgsGeometry()
        if polygon and polygon[0]:
            outer_rings.append(polygon[0])

    if not outer_rings:
        return QgsGeometry()

    if len(outer_rings) == 1:
        return QgsGeometry.fromPolylineXY(outer_rings[0])

    return QgsGeometry.fromMultiPolylineXY(outer_rings)


def _resolve_lot_id(feat, preferred_field: str = None) -> str:
    names = feat.fields().names()

    def _valid(field_name):
        if not field_name or field_name not in names:
            return None
        value = feat[field_name]
        if value is None:
            return None
        sval = str(value).strip()
        if not sval:
            return None
        if sval.lower() in {"null", "<null>", "none"}:
            return None
        return sval

    for candidate in [preferred_field, 'id', 'ID']:
        value = _valid(candidate)
        if value is not None:
            return value

    fid = feat.id()
    if fid is not None:
        return str(fid)
    return ""


def prepare_lots(lots_layer, excluded_geom: QgsGeometry, params) -> List[LotPrepared]:
    outputs = []
    for idx, feat in enumerate(collect_lot_features(lots_layer, params.selected_only), start=1):
        log = []
        lot_id = _resolve_lot_id(feat, getattr(params, 'lot_id_field', None))
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

        fence_line = make_valid(polygon_outer_boundary_as_line(fence_polygon))

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
