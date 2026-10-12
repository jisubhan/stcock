"""Versioned radar configuration, independent of legacy strategy overrides."""
from copy import deepcopy
from pathlib import Path
import os
import yaml

from app.settings import ROOT


def read_v12_config() -> dict:
    path = Path(os.environ.get('RADAR_CONFIG_PATH') or str(ROOT / 'config' / 'flowtrade-v1.2.yml'))
    raw = yaml.safe_load(path.read_text())
    config = raw.get('flowtrade_v1_2') if isinstance(raw, dict) else None
    if not isinstance(config, dict):
        raise ValueError('The v1.2 radar configuration is missing.')
    if config.get('feature_flags', {}).get('disable_order_execution') is not True:
        raise ValueError('Order execution must remain disabled.')
    if config.get('feature_flags', {}).get('require_provenance_for_news') is not True:
        raise ValueError('News provenance validation must remain enabled.')
    if config.get('data', {}).get('timezone') != 'Asia/Seoul':
        raise ValueError('The radar requires the Asia/Seoul timezone.')
    return deepcopy(config)
