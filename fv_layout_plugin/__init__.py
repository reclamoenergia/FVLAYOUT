# -*- coding: utf-8 -*-
"""QGIS plugin entry point."""


def classFactory(iface):
    from .plugin_main import FvLayoutPlugin

    return FvLayoutPlugin(iface)
