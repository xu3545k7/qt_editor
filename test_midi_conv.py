import sys
sys.path.insert(0, r'd:\Nostalgia\qt_editor')
from qt_editor.midi_to_xml_converter import MIDIToXMLConverter
from mido import MidiFile, MidiTrack, Message, MetaMessage

mid = MidiFile()
track = MidiTrack()
mid.tracks.append(track)
track.append(MetaMessage('set_tempo', tempo=500000, time=0))
track.append(Message('note_on', note=60, velocity=64, time=0))
track.append(Message('note_off', note=60, velocity=64, time=480))

midi_path = r'd:\Nostalgia\qt_editor\test_midi_conv.mid'
mid.save(midi_path)
out = r'd:\Nostalgia\qt_editor\test_midi_conv.xml'
MIDIToXMLConverter().convert_midi_to_xml(midi_path, out)
print('WROTE', out)
