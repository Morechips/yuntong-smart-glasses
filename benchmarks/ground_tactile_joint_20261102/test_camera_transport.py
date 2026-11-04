"""Check the diagnostic clock interval, independent of any physical device."""
import unittest
from check_camera_transport import statistics, transport_gates


class CameraTimingTests(unittest.TestCase):
    def sample(self, received, source, boot='test'):
        return dict(received=received, headers={'x-device-boot-id': boot, 'x-frame-time-status': 'ok',
                                               'x-frame-source-us': str(round(source * 1000000))})

    def data(self, rows):
        return dict(before=dict(t0=100., t1=100.01, esp_uptime_s=10., boot='test'), rows=rows)

    def test_clock_bracket_is_conservative_including_ms_rounding(self):
        data = self.data([self.sample(100.02, 9.98)])
        result = statistics(data)
        self.assertAlmostEqual(data['rows'][0]['source_stamp_age_lower_ms'], 30.)
        self.assertAlmostEqual(data['rows'][0]['source_stamp_age_upper_ms'], 41.)
        self.assertEqual(result['source_stamp_age_definitely_over500'], 0)

    def test_old_frame_and_receipt_gap_are_counted_separately(self):
        result = statistics(self.data([self.sample(100.02, 9.98), self.sample(101.52, 10.05)]))
        self.assertEqual(result['receive_gaps_over500'], 1)
        self.assertEqual(result['source_stamp_age_definitely_over500'], 1)

    def test_boot_change_cannot_reuse_clock_offset(self):
        with self.assertRaisesRegex(RuntimeError, 'boot changed'):
            statistics(self.data([self.sample(100.02, 9.98, boot='other')]))

    def test_successful_collection_does_not_hide_failed_transport(self):
        phase=dict(frames=30, receive_gaps_over500=1, source_stamp_age_definitely_over500=2)
        result=transport_gates(dict(rdk_direct=dict(phase,error='timeout'),pc_relay=dict(phase,reconnects=1)))
        self.assertFalse(result['rdk_direct_completed'])
        self.assertFalse(result['pc_relay_no_reconnect'])
        self.assertFalse(all(result.values()))

    def test_upper_age_is_used_to_keep_clock_uncertainty_visible(self):
        result=statistics(self.data([self.sample(100.52,10.01)]))
        self.assertEqual(result['source_stamp_age_definitely_over500'],0)
        self.assertEqual(result['source_stamp_age_possibly_over500'],1)
        self.assertGreater(result['source_stamp_age_upper_max_ms'],500)

    def test_bad_source_status_is_not_a_fresh_sample(self):
        row=self.sample(100.02,9.98);row['headers']['x-frame-time-status']='non-monotonic'
        result=statistics(self.data([row]))
        self.assertEqual(result['source_stamp_invalid'],1)
        self.assertIsNone(result['source_stamp_age_upper_max_ms'])

    def test_local_clock_quantization_widens_both_age_bounds(self):
        data=self.data([self.sample(100.02,9.98)])
        data['before']['clock_resolution_s']=.015625
        result=statistics(data)
        self.assertAlmostEqual(result['source_stamp_age_lower_max_ms'],30.-31.25)
        self.assertAlmostEqual(result['source_stamp_age_upper_max_ms'],41.+31.25)


if __name__ == '__main__':
    unittest.main()
