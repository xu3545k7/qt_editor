from lxml import etree
from typing import List, Optional

class GNote:
    def __init__(self, elem: etree.Element, idx: int):
        self.elem = elem
        self.idx = idx
        self.start = int(elem.get('start_timing_msec', '0'))
        self.end = int(elem.get('end_timing_msec', '0'))
        self.gate = int(elem.get('gate_time_msec', '0'))
        self.min_key = int(elem.get('min_key_index', '0'))
        self.max_key = int(elem.get('max_key_index', '0'))
        self.note_type = int(elem.get('note_type', '0'))
        self.hand = int(elem.get('hand', '0'))
        self.track = elem.get('track', None)
        self.pitch = int(elem.get('scale_piano', '0'))
        self.sub_elems = []
        sroot = elem.find('sub_note_data')
        if sroot is not None:
            self.sub_elems = [se for se in sroot]

    def to_dict(self):
        return {
            'start': self.start,
            'end': self.end,
            'gate': self.gate,
            'min_key': self.min_key,
            'max_key': self.max_key,
            'note_type': self.note_type,
            'hand': self.hand,
            'track': self.track,
            'pitch': self.pitch,
            'sub_elems': self.sub_elems,
        }

    def update_elem(self):
        self.elem.set('start_timing_msec', str(self.start))
        self.elem.set('end_timing_msec', str(self.end))
        self.elem.set('gate_time_msec', str(self.gate))
        self.elem.set('min_key_index', str(self.min_key))
        self.elem.set('max_key_index', str(self.max_key))
        self.elem.set('note_type', str(self.note_type))
        self.elem.set('hand', str(self.hand))
        if self.track is not None:
            self.elem.set('track', str(self.track))
        self.elem.set('scale_piano', str(self.pitch))
        # sub_elems not updated here

class NoteFile:
    def __init__(self, path: str):
        self.path = path
        self.tree = None
        self.root = None
        self.notes: List[GNote] = []
        self.bpm = 120.0
        self.beats_per_bar = 4
        self.beat_offset_ms = 0.0

    def load(self):
        self.tree = etree.parse(self.path)
        self.root = self.tree.getroot()
        # 讀取 header
        header = self.root.find('header')
        if header is not None:
            bpm = header.find('first_bpm')
            if bpm is not None:
                self.bpm = float(bpm.text)
            bar = header.find('time_signature_numerator')
            if bar is not None:
                self.beats_per_bar = int(bar.text)
            offset = header.find('beat_offset_ms')
            if offset is not None:
                self.beat_offset_ms = float(offset.text)
        # 讀取 beat_data（包含每個 beat 的時間）
        self.beats = []
        beat_root = self.root.find('beat_data')
        if beat_root is not None:
            for b in beat_root.findall('beat'):
                try:
                    idx_elem = b.find('index')
                    start_elem = b.find('start_timing_msec')
                    if idx_elem is not None and start_elem is not None:
                        idx = int(idx_elem.text)
                        ms = float(start_elem.text)
                        self.beats.append((idx, ms))
                except Exception:
                    continue

        nd = self.root.find('note_data')
        if nd is None:
            return False
        self.notes = [GNote(ne, i) for i, ne in enumerate(nd.findall('note'))]
        return True

    def save(self, path: Optional[str] = None):
        for note in self.notes:
            note.update_elem()
        self.tree.write(path or self.path, encoding='utf-8', pretty_print=True)

    def to_dict_list(self):
        return [n.to_dict() for n in self.notes]
