import math
from array import array

import pygame


class GameAudio:
    def __init__(self):
        self.enabled = False
        self.sample_rate = 44100
        self.bgm_channel = None
        self.sounds = {}

        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=self.sample_rate, size=-16, channels=1, buffer=512)
            self.enabled = True
            self.bgm_channel = pygame.mixer.Channel(0)
            self.sounds = {
                "bgm": self._create_bgm_loop(),
                "dice": self._create_note_sequence([(392.0, 0.08, 0.22), (523.25, 0.12, 0.2)]),
                "build": self._create_note_sequence([(523.25, 0.07, 0.18), (659.25, 0.1, 0.15)]),
                "road": self._create_note_sequence([(392.0, 0.05, 0.18), (440.0, 0.08, 0.12)]),
                "card": self._create_note_sequence([(659.25, 0.06, 0.17), (783.99, 0.1, 0.13)]),
                "robber": self._create_note_sequence([(220.0, 0.08, 0.25), (174.61, 0.1, 0.2)]),
                "victory": self._create_note_sequence(
                    [(523.25, 0.08, 0.18), (659.25, 0.08, 0.16), (783.99, 0.18, 0.15)]
                ),
                "error": self._create_note_sequence([(233.08, 0.05, 0.18), (207.65, 0.09, 0.15)]),
            }
        except Exception:
            self._disable()

    def _disable(self):
        self.enabled = False
        self.bgm_channel = None
        self.sounds = {}

    def _tone_samples(self, frequency, duration, volume=0.2):
        sample_count = max(1, int(self.sample_rate * duration))
        samples = array("h")
        for index in range(sample_count):
            phase = 2.0 * math.pi * frequency * index / self.sample_rate
            waveform = (
                math.sin(phase)
                + 0.35 * math.sin(phase * 2.0)
                + 0.18 * math.sin(phase * 3.0)
            ) / 1.53
            envelope = 1.0
            attack = int(sample_count * 0.08)
            release = int(sample_count * 0.18)
            if attack > 0 and index < attack:
                envelope = index / attack
            elif release > 0 and index > sample_count - release:
                envelope = max(0.0, (sample_count - index) / release)
            value = int(32767 * volume * waveform * envelope)
            samples.append(value)
        return samples

    def _create_note_sequence(self, notes):
        sequence = array("h")
        for frequency, duration, volume in notes:
            sequence.extend(self._tone_samples(frequency, duration, volume))
        return pygame.mixer.Sound(buffer=sequence.tobytes())

    def _create_bgm_loop(self):
        phrase = [
            (220.00, 0.24, 0.08),
            (277.18, 0.24, 0.06),
            (329.63, 0.24, 0.06),
            (392.00, 0.24, 0.05),
            (329.63, 0.24, 0.06),
            (277.18, 0.24, 0.06),
            (246.94, 0.24, 0.06),
            (329.63, 0.24, 0.05),
        ]
        notes = phrase + [(0.0, 0.1, 0.0)] + phrase[::-1]
        sequence = array("h")
        for frequency, duration, volume in notes:
            if frequency <= 0:
                sequence.extend(array("h", [0] * int(self.sample_rate * duration)))
                continue
            sequence.extend(self._tone_samples(frequency, duration, volume))
        return pygame.mixer.Sound(buffer=sequence.tobytes())

    def start_bgm(self):
        if not self.enabled or self.bgm_channel is None:
            return
        try:
            if self.bgm_channel.get_busy():
                return
            bgm_sound = self.sounds.get("bgm")
            if bgm_sound is None:
                return
            self.bgm_channel.set_volume(0.35)
            self.bgm_channel.play(bgm_sound, loops=-1)
        except Exception:
            self._disable()

    def play(self, name):
        if not self.enabled:
            return
        sound = self.sounds.get(name)
        if sound is None:
            return
        try:
            channel = pygame.mixer.find_channel()
            if channel is None:
                return
            channel.play(sound)
        except Exception:
            self._disable()

    def stop(self):
        if not self.enabled:
            return
        try:
            pygame.mixer.stop()
        except Exception:
            self._disable()
