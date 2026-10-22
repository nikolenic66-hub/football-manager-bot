"""League-level transfer market price pressure."""

def calculate_market_multiplier(league) -> float:
    total_budget = float(league.get('total_budget', 0) if isinstance(league, dict) else getattr(league, 'total_budget', 0))
    avg_income = float(league.get('avg_income', 0) if isinstance(league, dict) else getattr(league, 'avg_income', 0))
    tv_income = float(league.get('tv_income', 0) if isinstance(league, dict) else getattr(league, 'tv_income', 0))
    avg_income += tv_income
    budget_signal = min(1.0, total_budget / 100_000_000.0)
    income_signal = min(1.0, avg_income / 500_000.0)
    return round(max(0.8, min(1.5, 0.8 + 0.45 * budget_signal + 0.25 * income_signal)), 2)
