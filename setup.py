#!/usr/bin/env python3
"""Setup script for OneDrive Sync Client (ODSC)."""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="odsc",
    version="0.1.0",
    author="Marlo Bell",
    description="OneDrive Sync Client for Linux",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/marlobello/odsc",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
    python_requires=">=3.8",
    install_requires=[
        "requests>=2.31.0",
        "watchdog>=3.0.0",
        "PyGObject>=3.42.0",
        "dbus-python>=1.3.2",
        "python-dateutil>=2.8.2",
        "send2trash>=1.8.0",
        "cryptography>=41.0.0",
        "keyring>=24.0.0",
        "certifi>=2023.7.22",
    ],
    entry_points={
        "console_scripts": [
            "odsc=odsc.cli:main",
            "odsc-daemon=odsc.daemon:main",
            "odsc-gui=odsc.gui:main",
        ],
    },
)
