# -*- coding: utf-8 -*-

from unittest import TestCase
from rapidsms_httprouter_src.rapidsms_httprouter.utils import replace_characters


class UtilsTest(TestCase):
    def test_specific_characters_are_replaced(self):
        char_mapping = {'ç': 'c', 'à': 'a'}

        self.assertEqual('ca', replace_characters('çà', char_mapping))
        self.assertEqual('caaa45', replace_characters('çaaà45', char_mapping))
        self.assertEqual('haha', replace_characters('haha', char_mapping))
        self.assertEqual('caca', replace_characters('çàçà', char_mapping))
        self.assertEqual('ca có', replace_characters('çà çó', char_mapping))
        self.assertEqual('ca \n có', replace_characters('çà \n çó', char_mapping))

    def test_nothing_happens_when_char_mapping_empty(self):
        char_mapping = {}

        text = 'çà çó'
        self.assertEqual(text, replace_characters(text, char_mapping))


    def test_comparing_unicode(self):
        char_mapping = {'ç': 'c', 'à': 'a'}
        text = u'hahaha'

        self.assertEqual(text, replace_characters(text, char_mapping))
