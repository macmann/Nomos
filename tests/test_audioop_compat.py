import math
import unittest

from app import audioop_compat


class AudioopCompatRmsTests(unittest.TestCase):
    def test_rms_returns_zero_for_empty_fragment(self):
        self.assertEqual(audioop_compat.rms(b"", 2), 0)

    def test_rms_matches_pcm_energy_for_signed_16_bit_samples(self):
        samples = [0, 3000, -4000, 12000]
        fragment = b"".join(sample.to_bytes(2, "little", signed=True) for sample in samples)

        expected = math.isqrt(sum(sample * sample for sample in samples) // len(samples))

        self.assertEqual(audioop_compat.rms(fragment, 2), expected)

    def test_rms_rejects_partial_samples(self):
        with self.assertRaises(ValueError):
            audioop_compat.rms(b"\x00", 2)


if __name__ == "__main__":
    unittest.main()
