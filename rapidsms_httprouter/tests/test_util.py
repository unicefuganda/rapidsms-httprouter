# -*- coding: utf-8 -*-

from unittest import TestCase
from rapidsms.models import Backend, Connection
from rapidsms_httprouter_src.rapidsms_httprouter.models import Message
from rapidsms_httprouter_src.rapidsms_httprouter.utils import replace_characters, stringify
from mock import patch, Mock

INVALID_UNICODE_TEXT = "invalid unicode text"
VALID_UNICODE_TEXT = "valid unicode text"


def mock_str(text):
    if text == INVALID_UNICODE_TEXT:
        raise UnicodeEncodeError("ascii",  u'\xf9', 16, 128, INVALID_UNICODE_TEXT)
    return VALID_UNICODE_TEXT


def new_str_representation(text):
    return INVALID_UNICODE_TEXT


class UtilsTest(TestCase):

    @patch('__builtin__.str', mock_str)
    def test_incompatible_utf_8_text_are_stringified_2(self):
        Mock.__str__ = new_str_representation
        text = Mock(spec=str)
        text.encode = Mock(return_value = VALID_UNICODE_TEXT)

        self.assertEqual(VALID_UNICODE_TEXT, stringify(text))

    def test_specific_characters_are_replaced(self):
        char_mapping = {'ç': 'c', 'à': 'a', "ê": "e"}

        self.assertEqual('ca', replace_characters('çà', char_mapping))
        self.assertEqual('caaa45', replace_characters('çaaà45', char_mapping))
        self.assertEqual('haha', replace_characters('haha', char_mapping))
        self.assertEqual('caca', replace_characters('çàçà', char_mapping))
        self.assertEqual('ca có', replace_characters('çà çó', char_mapping))
        self.assertEqual('ca có e', replace_characters("çà çó ê", char_mapping))
        self.assertEqual('ca \n có', replace_characters('çà \n çó', char_mapping))

    def test_nothing_happens_when_char_mapping_empty(self):
        char_mapping = {}

        text = 'çà çó'
        self.assertEqual(text, replace_characters(text, char_mapping))

    def test_comparing_unicode(self):
        char_mapping = {'ç': 'c', 'à': 'a'}
        text = u'hahaha'

        self.assertEqual(text, replace_characters(text, char_mapping))

    def test_encode_returns_original_when_UnicodError_not_raise(self):
        text = 'çà çó'
        self.assertEqual(text, stringify(text))
