"""Compatibility entry point for verified Kiwi models."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model_assets import install

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    for item in json.loads(Path(__file__).with_name('models.json').read_text())['files']:
        install(args.destination / item['path'], item)
