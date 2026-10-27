"""Retrainable regression scorer for the expanding AI-readiness dataset."""

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class RegressionModel:
    coefficients: tuple
    intercept: float
    feature_names: tuple
    training_rows: int
    industry: str
    fiscal_year: str

    def predict(self, features: Sequence[float]) -> float:
        if len(features) != len(self.coefficients):
            raise ValueError("Feature count does not match the trained regression model.")
        value = self.intercept + sum(coefficient * feature for coefficient, feature in zip(self.coefficients, features))
        return round(max(0.0, min(100.0, value)), 1)


def train_regression(features, targets, feature_names, industry, fiscal_year, alpha=1.0, minimum_rows=3):
    """Train a Ridge regression model once reviewed training rows are available.

    Features should be normalized evidence metrics; targets should be reviewed
    0–100 scores. Industry/year are kept on the model to prevent cross-cohort use.
    """
    if len(features) != len(targets) or len(features) < minimum_rows:
        raise ValueError("Regression needs at least {} reviewed training rows.".format(minimum_rows))
    if not features or len(features[0]) != len(feature_names):
        raise ValueError("Feature names do not match the training matrix.")
    try:
        from sklearn.linear_model import Ridge
    except ImportError as exc:
        raise RuntimeError("Install scikit-learn before training regression models.") from exc
    model = Ridge(alpha=alpha)
    model.fit(features, targets)
    return RegressionModel(
        coefficients=tuple(float(value) for value in model.coef_),
        intercept=float(model.intercept_),
        feature_names=tuple(feature_names),
        training_rows=len(features),
        industry=industry,
        fiscal_year=fiscal_year,
    )
