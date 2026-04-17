"""
Setup para criar SurfShare.app

Primeiro instala as dependências:
  pip3 install pywebview py2app

Depois gera o app:
  python3 setup.py py2app
"""
from setuptools import setup

setup(
    name="OceanShare",
    app=["surfshare.py"],
    options={
        "py2app": {
            "argv_emulation": False,
            "packages": ["webview"],
            "includes": ["webview"],
            "iconfile": "icon.icns",
            "resources": ["ocean-icon.jpg"],
            "plist": {
                "CFBundleName": "OceanShare",
                "CFBundleDisplayName": "OceanShare",
                "CFBundleIdentifier": "com.oceanshare.app",
                "CFBundleVersion": "5.0.0",
                "CFBundleShortVersionString": "5.0",
                "LSMinimumSystemVersion": "10.15",
                "NSHumanReadableCopyright": "OceanShare 🌊",
                "NSAppTransportSecurity": {
                    "NSAllowsLocalNetworking": True,
                },
            },
        }
    },
    setup_requires=["py2app"],
)