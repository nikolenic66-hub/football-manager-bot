-- Rebalance template salaries to keep payroll sustainable while preserving market values.
UPDATE players
SET salary=GREATEST(15000,market_value/50)
WHERE is_seed=TRUE;
