import unittest

import numpy as np
import pandas as pd

from src.environmental_features import aggregate_daily_weather, spatial_lag


class EnvironmentalFeatureTests(unittest.TestCase):
    def test_daily_weather_aggregates_to_sunday_weeks(self):
        daily = pd.DataFrame({
            "time": ["2020-01-05", "2020-01-11", "2020-01-12"],
            "municipality_code": ["a", "a", "a"],
            "precipitation_sum": [1.0, 2.0, 4.0],
            "temperature_2m_mean": [20.0, 22.0, 24.0],
            "relative_humidity_2m_mean": [50.0, 60.0, 70.0],
        })

        weekly = aggregate_daily_weather(daily)

        self.assertEqual(weekly["week_start"].tolist(), [pd.Timestamp("2020-01-05"), pd.Timestamp("2020-01-12")])
        self.assertEqual(weekly["precipitation_mm"].tolist(), [3.0, 4.0])
        self.assertEqual(weekly["temperature_c"].tolist(), [21.0, 24.0])
        self.assertEqual(weekly["relative_humidity_pct"].tolist(), [55.0, 70.0])

    def test_spatial_lag_uses_row_normalized_neighbors_and_marks_isolates_missing(self):
        edges = pd.DataFrame({
            "municipality_code": ["a", "b", "b"],
            "neighbor_code": ["b", "a", "c"],
            "weight": [1.0, 0.5, 0.5],
        })
        cases = np.array([1, 2, 10, 20, 4, 20], dtype=np.float32)

        result = spatial_lag(cases, ["a", "b", "c"], 2, edges)

        np.testing.assert_allclose(result[:4], [10.0, 20.0, 2.5, 11.0])
        self.assertTrue(np.isnan(result[4:]).all())


if __name__ == "__main__":
    unittest.main()
