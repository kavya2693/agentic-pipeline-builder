# Stress runs

Scale 0.1 (about 40,000 orders). Run date 2026-02-01.

## Defect-rate sweep

| defect rate | types | PKs | FK recall | spurious FKs | DQ recall | row precision |
|---|---|---|---|---|---|---|
| 0.5% | 100.0% | 7/7 | 100% | 0 | 93.0% | 98.4% |
| 2.0% | 98.2% | 4/7 | 43% | 0 | 20.8% | 99.6% |
| 5.0% | 96.3% | 4/7 | 43% | 0 | 16.3% | 99.9% |
| 10.0% | 96.3% | 4/7 | 43% | 0 | 16.3% | 100.0% |

| defect | 0.5% | 2.0% | 5.0% | 10.0% |
|---|---|---|---|---|
| duplicate_row | 100% | 0% | 0% | 0% |
| future_date | 100% | 100% | 100% | 100% |
| invalid_enum | 64% | 0% | 0% | 0% |
| malformed_email | 100% | 100% | 100% | 100% |
| negative_value | 100% | 0% | 0% | 0% |
| null_required | 100% | 0% | 0% | 0% |
| orphan_fk | 100% | 70% | 70% | 70% |
| outlier_value | 64% | 0% | 0% | 0% |
| type_corruption | 100% | 29% | 0% | 0% |

## Same drops, rules frozen from last month's drop

Rules learned once on a separate 0.5% drop (seed 3), then enforced unchanged.

| defect rate | DQ recall | row precision | duplicate_row | future_date | invalid_enum | malformed_email | negative_value | null_required | orphan_fk | outlier_value | type_corruption |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.5% | 92.0% | 98.4% | 100% | 100% | 75% | 100% | 100% | 100% | 100% | 46% | 100% |
| 2.0% | 91.3% | 99.7% | 100% | 100% | 71% | 100% | 100% | 100% | 100% | 42% | 100% |
| 5.0% | 91.6% | 99.9% | 100% | 100% | 74% | 100% | 100% | 100% | 100% | 43% | 100% |
| 10.0% | 91.5% | 100.0% | 100% | 100% | 73% | 100% | 100% | 100% | 100% | 42% | 100% |

## Format shift

Changed: customers.signup_date -> 15.03.2024; orders.order_total -> 1.234,50; payments.paid_at -> +04:00.

Type accuracy 94.4%. Wrong: customers.signup_date: string (true date), orders.order_total: string (true decimal), payments.paid_at: string (true timestamp).
DQ recall 88.0%, row precision 98.5%.
