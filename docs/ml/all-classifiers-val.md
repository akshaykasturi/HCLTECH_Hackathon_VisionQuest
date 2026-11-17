# All ticket classifiers on the validation split

Generated 2026-11-17T09:48:38+00:00 by `backend/scripts/compare_all_classifiers.py`. Train 770, val 176. **Test was not used.**

Nine candidates on identical footing: Akhilesh's 6 TF-IDF models and Raswanth's 3 embedding models (bge-small-en-v1.5), every one wrapped in sigmoid calibration over the same train folds (grouped by root_cause), scored on the same val tickets with the same code.

Winner rule (Akhilesh's, applied to all 9): val macro-F1; unless the lead is not significant (McNemar p >= 0.05) and another candidate's ECE is >= 0.05 lower (then the best-F1 such candidate); within 0.01 -> lower ECE.

| Label | Candidate | By | Features | Macro-F1 | Accuracy | ECE | Brier | Fit s |
|---|---|---|---|---|---|---|---|---|
| category | rf | Akhilesh | tfidf | 0.830 | 0.830 | 0.266 | 0.185 | 6.0 |
| category | svm_C1 **(winner)** | Akhilesh | tfidf | 0.773 | 0.795 | 0.191 | 0.142 | 0.7 |
| category | emb_lr | Raswanth | embedding | 0.767 | 0.790 | 0.173 | 0.136 | 0.2 |
| category | emb_svm | Raswanth | embedding | 0.747 | 0.778 | 0.150 | 0.127 | 1.4 |
| category | lr_C10 | Akhilesh | tfidf | 0.735 | 0.767 | 0.166 | 0.146 | 1.0 |
| category | svm_C0.1 | Akhilesh | tfidf | 0.728 | 0.756 | 0.172 | 0.132 | 0.4 |
| category | lr_C1 | Akhilesh | tfidf | 0.728 | 0.761 | 0.188 | 0.146 | 1.1 |
| category | nb | Akhilesh | tfidf | 0.678 | 0.727 | 0.116 | 0.156 | 0.3 |
| category | emb_rf | Raswanth | embedding | 0.676 | 0.705 | 0.161 | 0.182 | 1.6 |
| subcategory | emb_lr **(winner)** | Raswanth | embedding | 0.848 | 0.864 | 0.307 | 0.199 | 0.5 |
| subcategory | rf | Akhilesh | tfidf | 0.808 | 0.812 | 0.333 | 0.203 | 6.4 |
| subcategory | svm_C1 (fallback) | Akhilesh | tfidf | 0.733 | 0.750 | 0.242 | 0.209 | 1.0 |
| subcategory | emb_rf | Raswanth | embedding | 0.714 | 0.733 | 0.301 | 0.237 | 4.4 |
| subcategory | svm_C0.1 | Akhilesh | tfidf | 0.689 | 0.710 | 0.196 | 0.188 | 0.7 |
| subcategory | lr_C10 | Akhilesh | tfidf | 0.687 | 0.722 | 0.223 | 0.212 | 1.6 |
| subcategory | lr_C1 | Akhilesh | tfidf | 0.667 | 0.699 | 0.196 | 0.200 | 1.8 |
| subcategory | emb_svm | Raswanth | embedding | 0.655 | 0.688 | 0.265 | 0.197 | 3.6 |
| subcategory | nb | Akhilesh | tfidf | 0.554 | 0.608 | 0.182 | 0.174 | 0.4 |
| priority | emb_rf | Raswanth | embedding | 0.297 | 0.455 | 0.075 | 0.255 | 3.8 |
| priority | svm_C1 **(winner)** | Akhilesh | tfidf | 0.291 | 0.517 | 0.060 | 0.262 | 0.5 |
| priority | nb | Akhilesh | tfidf | 0.285 | 0.500 | 0.126 | 0.253 | 0.3 |
| priority | svm_C0.1 | Akhilesh | tfidf | 0.285 | 0.500 | 0.084 | 0.256 | 0.4 |
| priority | lr_C1 | Akhilesh | tfidf | 0.284 | 0.500 | 0.068 | 0.256 | 0.9 |
| priority | lr_C10 | Akhilesh | tfidf | 0.283 | 0.506 | 0.047 | 0.255 | 1.3 |
| priority | rf | Akhilesh | tfidf | 0.258 | 0.449 | 0.065 | 0.258 | 7.6 |
| priority | emb_lr | Raswanth | embedding | 0.240 | 0.426 | 0.132 | 0.269 | 0.2 |
| priority | emb_svm | Raswanth | embedding | 0.238 | 0.426 | 0.113 | 0.263 | 0.8 |

| Label | Used by the app | Why | Winner macro-F1 (95% CI) | vs runner-up | TF-IDF fallback | Winner vs fallback |
|---|---|---|---|---|---|---|
| category | svm_C1 | calibration rule: rf's lead not significant (McNemar p 0.263) and ECE 0.191 vs 0.266 | 0.773 (0.702-0.827) | rf: p = 0.263 (b=7, c=13) | svm_C1 | - |
| subcategory | emb_lr | highest val macro-F1 | 0.848 (0.798-0.884) | rf: p = 0.200 (b=24, c=15) | svm_C1 | p = 0.002 (b=29, c=9) |
| priority | rule (no model good enough) | tie rule: within 0.01 macro-F1, lower ECE | 0.291 (0.254-0.327) | emb_rf: p = 0.145 (b=29, c=18) | svm_C1 | - |

## Combining the two winners (what the app runs)

Every subcategory belongs to exactly one category, so the category and subcategory winners are combined over valid pairs only. Category-first: the category model decides and the subcategory must belong to it. Joint: p_category x p_subcategory over valid pairs, category confidence = its share of that joint probability (`classify.decode`). Same val tickets, same winner rule.

| Decoding | Category accuracy | Category macro-F1 | Category ECE | Category Brier | Subcategory accuracy | Subcategory macro-F1 |
|---|---|---|---|---|---|---|
| joint **(used)** | 0.886 | 0.873 | 0.053 | 0.062 | 0.858 | 0.845 |
| category_first | 0.795 | 0.773 | 0.191 | 0.142 | 0.778 | 0.744 |

Why: highest val macro-F1. McNemar on category, joint vs the other: p = 0.000 (b=17, c=1).

Top category confusions of the winner on val (true -> predicted, count):

- Database -> Cloud: 7
- Infrastructure -> Collaboration: 4
- Security -> Collaboration: 4
- Cloud -> Database: 3
- Cloud -> Identity & Access: 3

Top subcategory confusions of the winner on val (true -> predicted, count):

- Endpoint Alert -> Monitoring: 8
- Monitoring -> DNS: 4
- Phishing -> ERP: 3
- Storage Access -> Account Lockout: 3
- Connectivity -> CRM: 1
