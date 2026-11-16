import unittest
from collector.sync import source_budget_available


class SourceBudgetTests(unittest.TestCase):
    def test_tail_of_batch_defers_source_without_claiming_it(self):
        self.assertFalse(source_budget_available(600,300,now=594))
        self.assertFalse(source_budget_available(600,300,now=301))

    def test_full_configured_budget_and_finalization_margin_are_required(self):
        self.assertTrue(source_budget_available(600,300,now=290))
        self.assertFalse(source_budget_available(600,300,now=291))
        self.assertTrue(source_budget_available(600,180,now=400))
