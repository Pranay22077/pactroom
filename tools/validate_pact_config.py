"""Offline validation only: never displays config contents or loads credentials.

Run from the checkout: python -m tools.validate_pact_config --config PATH
"""
import argparse
import json
from pathlib import Path
from app.pact import validate_workflows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding='utf-8'))
        enabled = validate_workflows(config)
    except (ValueError, KeyError, TypeError, OSError):
        raise SystemExit('INVALID: check required fields, actual identities, role separation and room mappings; contents omitted.')
    print(f'Configuration structure valid: {len(enabled)} enabled Pact workflow(s). No network or credential checks performed.')
    if not enabled: print('PactRoom is disabled; no Pact commands will be enabled.')


if __name__ == '__main__': main()
