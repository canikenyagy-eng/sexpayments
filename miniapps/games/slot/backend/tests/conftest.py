"""Put the service's ``backend/`` dir on sys.path so ``import game`` resolves
when running ``pytest`` from anywhere — keeps the standalone tests dependency-free.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
