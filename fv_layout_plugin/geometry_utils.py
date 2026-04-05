# -*- coding: utf-8 -*-
from typing import Iterable, List

from qgis.core import QgsFeature, QgsGeometry, QgsWkbTypes


def make_valid(geom: QgsGeometry) -> QgsGeometry:
    if geom is None or geom.isEmpty():
        return QgsGeometry()
    if not geom.isGeosValid():
        geom = geom.makeValid()
    return geom


def polygon_parts(geom: QgsGeometry) -> List[QgsGeometry]:
    if geom.isEmpty():
        return []
    single = QgsWkbTypes.isSingleType(geom.wkbType())
    if single:
        return [geom]
    out = []
    for part in geom.asGeometryCollection():
        if part.type() == QgsWkbTypes.PolygonGeometry:
            out.append(QgsGeometry(part))
    return out


def dissolve_features(features: Iterable[QgsFeature]) -> QgsGeometry:
    result = QgsGeometry()
    first = True
    for feat in features:
        g = make_valid(feat.geometry())
        if g.isEmpty():
            continue
        if first:
            result = QgsGeometry(g)
            first = False
        else:
            result = result.combine(g)
    return make_valid(result)


def safe_buffer(geom: QgsGeometry, distance: float, segments: int = 12) -> QgsGeometry:
    if geom.isEmpty():
        return QgsGeometry()
    out = geom.buffer(distance, segments)
    return make_valid(out)


def polygon_to_linestring(geom: QgsGeometry) -> QgsGeometry:
    boundary = geom.boundary()
    return make_valid(boundary)
