import unittest

import numpy as np

from src.compare_models import NUMERIC_FEATURES, _complete_feature_rows, score_predictions, select_best_config


class ModelComparisonTests(unittest.TestCase):
    def test_score_predictions_reports_count_errors(self):
        metrics = score_predictions(np.array([0, 10]), np.array([2, 8]))

        self.assertEqual(metrics["mae"], 2.0)
        self.assertEqual(metrics["rmse"], 2.0)
        self.assertEqual(metrics["wape"], 0.4)

    def test_select_best_config_uses_validation_wape_only(self):
        results = [
            {"params": {"num_leaves": 15}, "validation_wape": 0.35, "test_wape": 0.1},
            {"params": {"num_leaves": 31}, "validation_wape": 0.30, "test_wape": 0.5},
        ]

        selected = select_best_config(results)

        self.assertEqual(selected["params"], {"num_leaves": 31})

    def test_random_forest_input_drops_rows_with_missing_features(self):
        import pandas as pd

        frame = pd.DataFrame({feature: [2.0, 3.0] for feature in NUMERIC_FEATURES})
        frame.loc[0, "lag_52"] = float("nan")

        complete = _complete_feature_rows(frame)

        self.assertEqual(len(complete), 1)
        self.assertEqual(complete.iloc[0]["lag_0"], 3.0)


if __name__ == "__main__":
    unittest.main()
