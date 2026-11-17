# AHP weight sensitivity (weights only; ranking-outcome sensitivity not run, time limit)
Judgements: app/tickets/ahp_judgements.yaml. Base CR 0.013.
Base weights: dense_similarity 0.291, category_agreement 0.191, resolution_quality 0.171, bm25 0.142,
same_service 0.094, analyst_feedback 0.066, age_days 0.045.
One-at-a-time (each of 21 judgements moved one Saaty step up and down, 42 cases): max weight shift 0.026;
top-3 order unchanged in 40/42; max CR 0.025. Most sensitive: category_agreement vs resolution_quality (close weights).
Monte Carlo (5000 runs, every judgement randomly -1/0/+1 step): CR < 0.10 in 99.5%; dense_similarity stays first in
95.1%; 90% ranges: dense 0.227-0.328, category 0.147-0.235, resolution 0.132-0.220, bm25 0.108-0.184,
service 0.074-0.124, feedback 0.053-0.085, age 0.038-0.058.
Not measured: effect on the final ticket ranking (lam blend with entropy halves AHP's influence).
