"""Feature Importance: lightweight ML to rank which features matter.

Uses LogisticRegression and XGBoost to determine feature importance.
NOT for live trading — only for understanding which signals work.
"""

from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger()

FEATURE_KEYS = [
    "rsi", "atr", "volatility", "spread", "imbalance", "volume_delta",
    "trade_speed", "buy_pressure", "trend_strength", "trend_r2",
    "trend_consistency", "mom_ret_2m", "mom_ret_5m", "vol_ratio",
    "vpin", "hour_sin", "hour_cos", "is_weekend", "session",
]


class FeatureImportanceAnalyzer:
    """Ranks features by their predictive value for trade outcome."""

    def analyze(self, trades: list[dict], min_trades: int = 30) -> dict[str, Any]:
        """Run feature importance analysis.

        Returns ranking of features and their relative importance.
        """
        if len(trades) < min_trades:
            return {"error": f"Need at least {min_trades} trades, have {len(trades)}",
                    "ranking": [], "models": {}}

        # Build X (features) and y (labels)
        X, y, valid_features = self._build_dataset(trades)

        if len(X) < min_trades or len(valid_features) < 3:
            return {"error": "Not enough valid feature data", "ranking": [], "models": {}}

        results = {"models": {}, "ranking": []}

        # 1. Logistic Regression
        try:
            lr_importance = self._logistic_regression(X, y, valid_features)
            results["models"]["logistic_regression"] = lr_importance
        except Exception as e:
            results["models"]["logistic_regression"] = {"error": str(e)}

        # 2. XGBoost
        try:
            xgb_importance = self._xgboost(X, y, valid_features)
            results["models"]["xgboost"] = xgb_importance
        except Exception as e:
            results["models"]["xgboost"] = {"error": str(e)}

        # Combined ranking
        results["ranking"] = self._combined_ranking(results["models"], valid_features)

        return results

    def _build_dataset(self, trades: list[dict]) -> tuple:
        """Extract feature matrix and labels."""
        rows = []
        labels = []
        valid_features = []

        # Determine which features have data
        for fkey in FEATURE_KEYS:
            has_data = sum(1 for t in trades if t.get("features", {}).get(fkey) is not None)
            if has_data > len(trades) * 0.5:  # at least 50% have this feature
                valid_features.append(fkey)

        for trade in trades:
            features = trade.get("features", {})
            row = []
            valid = True
            for fkey in valid_features:
                val = features.get(fkey)
                if val is None:
                    valid = False
                    break
                row.append(float(val))

            if valid:
                rows.append(row)
                labels.append(1 if (trade.get("pnl", 0) or 0) > 0 else 0)

        return np.array(rows), np.array(labels), valid_features

    def _logistic_regression(self, X: np.ndarray, y: np.ndarray,
                              features: list[str]) -> dict[str, Any]:
        """Logistic regression feature importance."""
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.model_selection import cross_val_score

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        model = LogisticRegression(max_iter=1000, random_state=42)
        scores = cross_val_score(model, X_scaled, y, cv=min(5, len(y) // 5), scoring="accuracy")
        model.fit(X_scaled, y)

        importance = np.abs(model.coef_[0])
        importance_norm = importance / importance.sum() * 100

        ranking = sorted(zip(features, importance_norm), key=lambda x: x[1], reverse=True)

        return {
            "accuracy": round(float(scores.mean()), 4),
            "accuracy_std": round(float(scores.std()), 4),
            "features": [{"name": f, "importance": round(float(imp), 2)} for f, imp in ranking],
        }

    def _xgboost(self, X: np.ndarray, y: np.ndarray,
                  features: list[str]) -> dict[str, Any]:
        """XGBoost feature importance."""
        from xgboost import XGBClassifier
        from sklearn.model_selection import cross_val_score

        model = XGBClassifier(
            n_estimators=50, max_depth=3, learning_rate=0.1,
            random_state=42, verbosity=0, use_label_encoder=False,
            eval_metric="logloss",
        )
        scores = cross_val_score(model, X, y, cv=min(5, len(y) // 5), scoring="accuracy")
        model.fit(X, y)

        importance = model.feature_importances_
        importance_norm = importance / importance.sum() * 100

        ranking = sorted(zip(features, importance_norm), key=lambda x: x[1], reverse=True)

        return {
            "accuracy": round(float(scores.mean()), 4),
            "accuracy_std": round(float(scores.std()), 4),
            "features": [{"name": f, "importance": round(float(imp), 2)} for f, imp in ranking],
        }

    def _combined_ranking(self, models: dict, features: list[str]) -> list[dict]:
        """Combine rankings from all models."""
        scores: dict[str, list[float]] = {f: [] for f in features}

        for model_name, model_data in models.items():
            if "error" in model_data:
                continue
            for feat_data in model_data.get("features", []):
                name = feat_data["name"]
                if name in scores:
                    scores[name].append(feat_data["importance"])

        combined = []
        for feat, imps in scores.items():
            if imps:
                combined.append({
                    "name": feat,
                    "importance": round(float(np.mean(imps)), 2),
                    "agreement": round(float(np.std(imps)), 2) if len(imps) > 1 else 0,
                })

        return sorted(combined, key=lambda x: x["importance"], reverse=True)
