DEFAULTS = {'provider': 'Codex', 'monitor': True, 'voice': True, 'volume': 80, 'address': ''}


def validated(value):
    settings = DEFAULTS.copy()
    if not isinstance(value, dict):
        return settings
    for name in ('monitor', 'voice'):
        if type(value.get(name)) is bool:
            settings[name] = value[name]
    if type(value.get('volume')) is int and 0 <= value['volume'] <= 100:
        settings['volume'] = value['volume']
    address = value.get('address', '')
    if isinstance(address, str):
        import re
        if not address.strip() or re.fullmatch(r'[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}', address.strip()):
            settings['address'] = address.strip()
    return settings
