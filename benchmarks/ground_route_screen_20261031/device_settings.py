"""Device identity comes from ignored local JSON; passwords never enter config."""
from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[2]
_path = ROOT / 'config/device.local.json'
SETTINGS = dict(x5_host='192.168.127.10', x5_user='sunrise', x5_host_key='', esp_mac='')
if _path.is_file():
    SETTINGS.update(json.loads(_path.read_text(encoding='utf-8-sig')))
HOST = str(SETTINGS['x5_host'])
USER = str(SETTINGS['x5_user'])
EXPECTED_KEY = str(SETTINGS['x5_host_key'])
ESP_MAC = str(SETTINGS['esp_mac']).lower()


def require_x5_identity():
    if not HOST or not USER or not re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}', EXPECTED_KEY):
        raise RuntimeError('Configure x5_host, x5_user and trusted x5_host_key in config/device.local.json first')


def require_esp_identity():
    if not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', ESP_MAC):
        raise RuntimeError('Configure the actual esp_mac in config/device.local.json first')
