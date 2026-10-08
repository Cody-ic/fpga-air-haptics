"""Validate the document exported by Flutter tests using the desktop importer."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from desktop_app.model import config_from_document

export = ROOT / 'mobile_app/.runtime/phone-export.json'
fixture = ROOT / 'mobile_app/test/fixtures/python_reference.json'
actual = config_from_document(json.loads(export.read_text(encoding='utf-8')))
expected = config_from_document(json.loads(fixture.read_text(encoding='utf-8'))['document'])
assert actual == expected, 'Mobile export changed coordinates or configuration'
print('Desktop importer accepts the mobile export with every configuration field preserved.')
