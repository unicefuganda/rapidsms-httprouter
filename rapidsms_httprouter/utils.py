# -*- coding: utf-8 -*-

def replace_characters(text, character_mapping):
    result = str(text)
    for char, replacement_char in character_mapping.items():
        result = result.replace(char, replacement_char)
    return result