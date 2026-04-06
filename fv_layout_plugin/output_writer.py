# -*- coding: utf-8 -*-
import csv
import os
from typing import Dict, List

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsFeature,
    QgsField,
    QgsFields,
    QgsFillSymbol,
    QgsGeometry,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
)


def _new_layer(geom: str, crs_authid: str, name: str, fields: QgsFields):
    layer = QgsVectorLayer(f"{geom}?crs={crs_authid}", name, "memory")
    if not layer.isValid():
        raise RuntimeError(f"Impossibile creare layer memory {name} con geometria {geom}")
    pr = layer.dataProvider()
    pr.addAttributes(list(fields))
    layer.updateFields()
    return layer


def _save_layer(layer: QgsVectorLayer, gpkg_path: str, layer_name: str, create_file: bool = False):
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.layerName = layer_name
    opts.actionOnExistingFile = (
        QgsVectorFileWriter.CreateOrOverwriteFile
        if create_file
        else QgsVectorFileWriter.CreateOrOverwriteLayer
    )
    err, new_path, error_message, _ = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer,
        gpkg_path,
        QgsProject.instance().transformContext(),
        opts,
    )
    if err != QgsVectorFileWriter.NoError:
        raise RuntimeError(
            f"Errore salvataggio layer {layer_name} "
            f"(codice={int(err)}, messaggio={error_message!r}, output={new_path!r})"
        )


def _add_features_or_raise(layer: QgsVectorLayer, feats: List[QgsFeature], context: str):
    ok, added_feats = layer.dataProvider().addFeatures(feats)
    added_count = len(added_feats) if added_feats is not None else 0
    expected_count = len(feats)
    if expected_count == 0:
        return
    if not ok or added_count == 0:
        raise RuntimeError(
            f"Errore aggiunta feature su layer {layer.name()} ({context}): "
            f"attese={expected_count}, aggiunte={added_count}"
        )


def _style_layer(layer, kind):
    if kind == "line":
        layer.setRenderer(layer.renderer().clone())
        layer.renderer().setSymbol(QgsLineSymbol.createSimple({"color": "227,26,28", "width": "0.7"}))
    elif kind == "road":
        layer.renderer().setSymbol(QgsFillSymbol.createSimple({"color": "180,180,180,120", "outline_color": "90,90,90"}))
    elif kind == "usable":
        layer.renderer().setSymbol(QgsFillSymbol.createSimple({"color": "51,160,44,80", "outline_color": "31,120,180"}))
    elif kind == "table":
        layer.renderer().setSymbol(QgsFillSymbol.createSimple({"color": "255,127,0,120", "outline_color": "230,90,0"}))
    elif kind == "point":
        layer.renderer().setSymbol(QgsMarkerSymbol.createSimple({"color": "106,61,154", "size": "1.8"}))


class OutputWriter:
    def __init__(self, params, crs_authid: str):
        self.params = params
        self.crs_authid = crs_authid

    def write(self, results: List[Dict]):
        gpkg = os.path.join(self.params.output_dir, f"{self.params.output_prefix}_layout.gpkg")
        csv_path = os.path.join(self.params.output_dir, f"{self.params.output_prefix}_report.csv")
        if os.path.exists(gpkg):
            os.remove(gpkg)

        fence = self._build_fence(results)
        road = self._build_road(results)
        usable = self._build_usable(results)
        rows = self._build_rows(results)
        tables = self._build_tables(results)
        centroids = self._build_centroids(tables)

        mapping = [
            (fence, "RECINZIONE", "line"),
            (road, "VIABILITA_PERIMETRALE", "road"),
            (usable, "AREE_INSTALLABILI", "usable"),
            (rows, "FILE_FV", "line"),
            (tables, "TAVOLI_FV", "table"),
            (centroids, "TAVOLI_FV_CENTROIDI", "point"),
        ]
        for idx, (lyr, name, kind) in enumerate(mapping):
            _style_layer(lyr, kind)
            _save_layer(lyr, gpkg, name, create_file=(idx == 0))

        self._write_report(csv_path, results)

        if self.params.add_to_project:
            failed_to_load = []
            for _, name, _ in mapping:
                layer = QgsVectorLayer(f"{gpkg}|layername={name}", name, "ogr")
                if layer.isValid():
                    QgsProject.instance().addMapLayer(layer)
                else:
                    failed_to_load.append(name)
            if failed_to_load:
                raise RuntimeError(
                    "Layer salvati ma non caricati nel progetto: " + ", ".join(failed_to_load)
                )

        return {"gpkg": gpkg, "report": csv_path}

    def _build_fence(self, results):
        fields = QgsFields()
        fields.append(QgsField("lot_id", QVariant.String))
        fields.append(QgsField("lunghezza_m", QVariant.Double))
        lyr = _new_layer("MultiLineString", self.crs_authid, "RECINZIONE", fields)
        feats = []
        for r in results:
            f = QgsFeature(lyr.fields())
            f["lot_id"] = r["lot_id"]
            f["lunghezza_m"] = r["fence_line"].length()
            fence_geom = QgsGeometry(r["fence_line"])
            if fence_geom.isNull():
                raise RuntimeError(f"Geometria recinzione nulla per lotto {r['lot_id']}")
            if not fence_geom.isMultipart():
                if not fence_geom.convertToMultiType():
                    raise RuntimeError(
                        f"Impossibile convertire RECINZIONE in MultiLineString per lotto {r['lot_id']}"
                    )
            f.setGeometry(fence_geom)
            feats.append(f)
        _add_features_or_raise(lyr, feats, "recinzione")
        return lyr

    def _build_road(self, results):
        fields = QgsFields()
        fields.append(QgsField("lot_id", QVariant.String))
        fields.append(QgsField("area_m2", QVariant.Double))
        fields.append(QgsField("width_m", QVariant.Double))
        lyr = _new_layer("Polygon", self.crs_authid, "VIABILITA_PERIMETRALE", fields)
        feats = []
        for r in results:
            f = QgsFeature(lyr.fields())
            f["lot_id"] = r["lot_id"]
            f["area_m2"] = r["road_geom"].area()
            f["width_m"] = self.params.road_width_m
            f.setGeometry(r["road_geom"])
            feats.append(f)
        _add_features_or_raise(lyr, feats, "viabilita_perimetrale")
        return lyr

    def _build_usable(self, results):
        fields = QgsFields()
        fields.append(QgsField("lot_id", QVariant.String))
        fields.append(QgsField("area_id", QVariant.String))
        fields.append(QgsField("area_m2", QVariant.Double))
        fields.append(QgsField("slope_limit", QVariant.Double))
        fields.append(QgsField("usable_flag", QVariant.Int))
        lyr = _new_layer("Polygon", self.crs_authid, "AREE_INSTALLABILI", fields)
        feats = []
        for r in results:
            f = QgsFeature(lyr.fields())
            f["lot_id"] = r["lot_id"]
            f["area_id"] = f"{r['lot_id']}_A1"
            f["area_m2"] = r["usable_geom"].area()
            f["slope_limit"] = self.params.slope_limit_deg
            f["usable_flag"] = 1
            f.setGeometry(r["usable_geom"])
            feats.append(f)
        _add_features_or_raise(lyr, feats, "aree_installabili")
        return lyr

    def _build_rows(self, results):
        fields = QgsFields()
        for name, t in [
            ("lot_id", QVariant.String),
            ("area_id", QVariant.String),
            ("row_id", QVariant.Int),
            ("azimuth", QVariant.Double),
            ("row_length_m", QVariant.Double),
            ("shift_m", QVariant.Double),
        ]:
            fields.append(QgsField(name, t))
        lyr = _new_layer("LineString", self.crs_authid, "FILE_FV", fields)
        feats = []
        for r in results:
            for rr in r["rows"]:
                f = QgsFeature(lyr.fields())
                f["lot_id"] = r["lot_id"]
                f["area_id"] = f"{r['lot_id']}_A1"
                f["row_id"] = rr.row_id
                f["azimuth"] = rr.azimuth
                f["row_length_m"] = rr.geom.length()
                f["shift_m"] = rr.shift_m
                f.setGeometry(rr.geom)
                feats.append(f)
        _add_features_or_raise(lyr, feats, "file_fv")
        return lyr

    def _build_tables(self, results):
        fields = QgsFields()
        cols = [
            ("lot_id", QVariant.String),
            ("area_id", QVariant.String),
            ("row_id", QVariant.Int),
            ("table_id", QVariant.Int),
            ("structure_type", QVariant.String),
            ("azimuth", QVariant.Double),
            ("shift_m", QVariant.Double),
            ("length_m", QVariant.Double),
            ("width_m", QVariant.Double),
            ("area_m2", QVariant.Double),
            ("modules_n", QVariant.Int),
            ("power_kw", QVariant.Double),
            ("z_mean", QVariant.Double),
            ("z_min", QVariant.Double),
            ("z_max", QVariant.Double),
            ("slope_mean", QVariant.Double),
            ("valid_flag", QVariant.Int),
        ]
        for c, t in cols:
            fields.append(QgsField(c, t))
        lyr = _new_layer("Polygon", self.crs_authid, "TAVOLI_FV", fields)
        feats = []
        for r in results:
            for tb in r["tables"]:
                f = QgsFeature(lyr.fields())
                f["lot_id"] = r["lot_id"]
                f["area_id"] = f"{r['lot_id']}_A1"
                f["row_id"] = tb["row_id"]
                f["table_id"] = tb["table_id"]
                f["structure_type"] = self.params.structure_type
                f["azimuth"] = tb["azimuth"]
                f["shift_m"] = tb["shift_m"]
                f["length_m"] = self.params.table_length_m
                f["width_m"] = self.params.table_width_m
                f["area_m2"] = tb["geom"].area()
                f["modules_n"] = self.params.modules_per_table
                f["power_kw"] = self.params.table_power_kw
                f["z_mean"] = tb["terrain"].get("z_mean")
                f["z_min"] = tb["terrain"].get("z_min")
                f["z_max"] = tb["terrain"].get("z_max")
                f["slope_mean"] = tb["terrain"].get("slope_mean")
                f["valid_flag"] = 1
                f.setGeometry(tb["geom"])
                feats.append(f)
        _add_features_or_raise(lyr, feats, "tavoli_fv")
        return lyr

    def _build_centroids(self, table_layer):
        lyr = QgsVectorLayer(f"Point?crs={self.crs_authid}", "TAVOLI_FV_CENTROIDI", "memory")
        lyr.dataProvider().addAttributes(table_layer.fields())
        lyr.updateFields()
        feats = []
        for feat in table_layer.getFeatures():
            c = QgsFeature(lyr.fields())
            for fld in lyr.fields().names():
                c[fld] = feat[fld]
            c.setGeometry(feat.geometry().centroid())
            feats.append(c)
        _add_features_or_raise(lyr, feats, "tavoli_fv_centroidi")
        return lyr

    def _write_report(self, csv_path, results):
        fields = [
            "lot_id",
            "area_catastale_m2",
            "area_esclusa_m2",
            "area_recinzione_interna_m2",
            "area_viabilita_m2",
            "area_netta_installabile_m2",
            "numero_file",
            "numero_tavoli",
            "numero_moduli",
            "potenza_totale_kw",
            "azimuth_ottimo",
            "shift_ottimo",
        ]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, fieldnames=fields)
            wr.writeheader()
            for r in results:
                nt = len(r["tables"])
                wr.writerow(
                    {
                        "lot_id": r["lot_id"],
                        "area_catastale_m2": r["stats"]["area_catastale_m2"],
                        "area_esclusa_m2": r["stats"]["area_esclusa_m2"],
                        "area_recinzione_interna_m2": r["stats"]["area_recinzione_interna_m2"],
                        "area_viabilita_m2": r["stats"]["area_viabilita_m2"],
                        "area_netta_installabile_m2": r["usable_geom"].area(),
                        "numero_file": len(r["rows"]),
                        "numero_tavoli": nt,
                        "numero_moduli": nt * self.params.modules_per_table,
                        "potenza_totale_kw": nt * self.params.table_power_kw,
                        "azimuth_ottimo": r["azimuth"],
                        "shift_ottimo": r["shift"],
                    }
                )
