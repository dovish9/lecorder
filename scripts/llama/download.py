"""Compatibility entry point for the pinned llama.cpp model assets."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model_assets import download

if __name__ == '__main__':
    download('llama')
