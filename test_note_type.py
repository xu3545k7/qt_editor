import unittest

from qt_editor.models import (
    note_is_long, note_is_staccato, note_is_slide, note_is_trill,
    note_has_duration, note_type_to_str,
)


class NoteTypeStaccatoTests(unittest.TestCase):
    def test_staccato_is_not_long(self):
        # staccato(3)=0b11 含 LONG_BIT(0x02)，但不能被誤判成 hold
        self.assertTrue(note_is_staccato(3))
        self.assertFalse(note_is_long(3))
        self.assertFalse(note_has_duration(3))
        self.assertFalse(note_is_slide(3))
        self.assertFalse(note_is_trill(3))

    def test_staccato_serializes_as_staccato_not_hold(self):
        self.assertEqual(note_type_to_str(3), 'staccato')

    def test_real_long_still_detected(self):
        self.assertTrue(note_is_long(2))
        self.assertTrue(note_has_duration(2))
        self.assertEqual(note_type_to_str(2), 'hold')
        # long|skin
        self.assertTrue(note_is_long(10))
        self.assertEqual(note_type_to_str(10), 'hold')

    def test_other_types_unaffected(self):
        self.assertEqual(note_type_to_str(0), 'tap')
        self.assertEqual(note_type_to_str(1), 'soft')
        self.assertFalse(note_is_long(1))
        self.assertEqual(note_type_to_str(4), 'slide')
        self.assertEqual(note_type_to_str(64), 'trill')


if __name__ == '__main__':
    unittest.main()
