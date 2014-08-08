# -*- coding: utf-8 -*-

def replace_characters(text, character_mapping):
    result = stringify(text)
    for char, replacement_char in character_mapping.items():
        result = result.replace(char, replacement_char)
    return result

def stringify(text, coding='UTF-8'):
    try:
        return str(text)
    except UnicodeEncodeError:
        return str(text.encode(coding))
