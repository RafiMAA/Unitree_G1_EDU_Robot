#!/usr/bin/env python3
"""Keep speech/RAG dependencies separate from the robot simulation environment."""
import os
from pathlib import Path
import subprocess
import sys

# Clear ROS/simulation paths before pip checks installed dependencies.
os.environ.pop('PYTHONPATH', None)
os.environ['PYTHONNOUSERSITE'] = '1'
ROOT = Path(__file__).resolve().parents[2]
ENV = ROOT / 'g1-ros2-workspace/.rag-venv'
requirements = Path(__file__).with_name('rag-requirements.txt')
python = Path(os.environ.get('G1_RAG_PYTHON', ENV / 'bin/python'))
if not os.environ.get('G1_RAG_PYTHON'):
    if not python.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(ENV)], check=True)
    marker = ENV / '.console-requirements'
    content = requirements.read_text()
    if not marker.exists() or marker.read_text() != content:
        print('Preparing the conversation environment…', flush=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)], check=True)
        marker.write_text(content)
# Do not inherit the simulation's numpy/ONNX dependencies via PYTHONPATH.
os.environ['PYTHONPATH'] = str(ROOT / 'g1-ros2-workspace/src/g1_conversation')
os.environ['PYTHONNOUSERSITE'] = '1'
os.execv(str(python), [str(python), str(Path(__file__).with_name('rag_worker.py'))])
