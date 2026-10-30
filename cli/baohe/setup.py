from setuptools import find_packages, setup

setup(
    name="baohe",
    version="0.1.0",
    description="Read-only CLI to extract 宝盒lite chat caches",
    packages=find_packages(include=["baohe*"]),
    python_requires=">=3.9",
    extras_require={"dev": ["pytest>=7.0"]},
    entry_points={"console_scripts": ["baohe = baohe.cli:main"]},
)
