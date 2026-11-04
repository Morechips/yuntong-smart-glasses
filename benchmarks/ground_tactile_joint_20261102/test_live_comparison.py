"""The stability gate must not confuse a fast p95 with fresh continuous input."""
import unittest
from check_live_comparison import gates


class ComparisonTests(unittest.TestCase):
    def sample(self):
        return dict(error=None,tactile_error=None,results=100,receive_to_result_p95_ms=60,
            floor_update_gap_run_max_ms=200,camera_reconnects=0,speech_acknowledgments=0,haptic_acknowledgments=0,
            transport_trace_error=None,transport_trace=dict(frames=120,frames_total=120,source_stamp_invalid=0,
                source_stamp_age_upper_max_ms=200,receive_gaps_over500=0))

    def test_fast_results_cannot_hide_long_update_gap(self):
        sample=self.sample();sample['floor_update_gap_run_max_ms']=1500
        self.assertFalse(all(gates(sample).values()))

    def test_missing_clock_trace_cannot_pass(self):
        sample=self.sample();sample['transport_trace']=None
        self.assertFalse(all(gates(sample).values()))

    def test_fresh_receipt_cannot_hide_old_source_or_partial_sample(self):
        sample=self.sample();sample['transport_trace']['source_stamp_age_upper_max_ms']=1700
        self.assertFalse(gates(sample)['source_age_upper_max_le500'])
        sample=self.sample();sample['transport_trace']['frames_total']=9000
        self.assertFalse(gates(sample)['trace_covers_all_received'])

    def test_complete_fresh_silent_sample_passes(self):
        self.assertTrue(all(gates(self.sample()).values()))


if __name__=='__main__':unittest.main()
