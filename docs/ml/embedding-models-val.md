# Embedding classifiers on the validation split

Generated 2026-11-17T08:43:49+00:00 by `backend/scripts/compare_embedding_models.py`. Features: BAAI/bge-small-en-v1.5 embeddings (384 numbers) of `ticket_text(subject, description)`. Tuned on train (5-fold GridSearchCV, macro-F1), compared on val. **Test was not used.** For the evaluation report only: the app does not use these models.

Brier = top-class confidence vs right/wrong (0 is perfect). Majority's confidence is set to 1, so its Brier = its error rate. TF-IDF fit time includes predicting val.

## category

770 train / 176 val tickets, 9 classes.

| model | accuracy | macro-P | macro-R | macro-F1 | Brier | fit time |
|---|---|---|---|---|---|---|
| logistic_regression **(winner)** | 0.818 | 0.838 | 0.799 | 0.793 | 0.088 | 0.04 s |
| linear_svm_calibrated | 0.761 | 0.780 | 0.729 | 0.721 | 0.120 | 2.48 s |
| random_forest | 0.653 | 0.651 | 0.604 | 0.596 | 0.306 | 0.65 s |
| _ref: majority_ | 0.136 | 0.015 | 0.111 | 0.027 | 0.864 | 0.00 s |
| _ref: tfidf_logistic_regression_ | 0.778 | 0.753 | 0.741 | 0.727 | 0.314 | 0.90 s |

- logistic_regression vs linear_svm_calibrated: 13 val tickets only logistic_regression got right, 3 only linear_svm_calibrated got right; p = 0.0213 -> a real difference, in favour of logistic_regression (p < 0.05).
- logistic_regression vs tfidf_logistic_regression: 22 val tickets only logistic_regression got right, 15 only tfidf_logistic_regression got right; p = 0.324 -> could be chance (p >= 0.05).

Confusion matrix of the winner (logistic_regression), rows = true, columns = predicted:

| true \ predicted | Business Applications | Cloud | Collaboration | Database | Endpoint | Identity & Access | Infrastructure | Network | Security |
|---|---|---|---|---|---|---|---|---|---|
| **Business Applications** | 15 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| **Cloud** | 0 | 12 | 0 | 0 | 1 | 3 | 0 | 0 | 0 |
| **Collaboration** | 0 | 0 | 16 | 0 | 0 | 0 | 0 | 0 | 0 |
| **Database** | 1 | 7 | 0 | 7 | 0 | 0 | 1 | 0 | 0 |
| **Endpoint** | 0 | 0 | 4 | 0 | 16 | 1 | 2 | 0 | 1 |
| **Identity & Access** | 0 | 0 | 0 | 0 | 0 | 24 | 0 | 0 | 0 |
| **Infrastructure** | 1 | 0 | 0 | 0 | 0 | 0 | 23 | 0 | 0 |
| **Network** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 24 | 0 |
| **Security** | 0 | 0 | 0 | 0 | 2 | 0 | 7 | 0 | 7 |

Most confused pairs (true -> predicted):

1. Database -> Cloud: 7
2. Security -> Infrastructure: 7
3. Endpoint -> Collaboration: 4
4. Cloud -> Identity & Access: 3
5. Endpoint -> Infrastructure: 2

## subcategory

770 train / 176 val tickets, 22 classes.

| model | accuracy | macro-P | macro-R | macro-F1 | Brier | fit time |
|---|---|---|---|---|---|---|
| logistic_regression **(winner)** | 0.852 | 0.844 | 0.852 | 0.836 | 0.162 | 0.14 s |
| linear_svm_calibrated | 0.727 | 0.770 | 0.727 | 0.706 | 0.163 | 6.02 s |
| random_forest | 0.722 | 0.740 | 0.722 | 0.704 | 0.459 | 1.66 s |
| _ref: majority_ | 0.045 | 0.002 | 0.045 | 0.004 | 0.955 | 0.00 s |
| _ref: tfidf_logistic_regression_ | 0.710 | 0.740 | 0.710 | 0.696 | 0.463 | 1.23 s |

- logistic_regression vs linear_svm_calibrated: 24 val tickets only logistic_regression got right, 2 only linear_svm_calibrated got right; p = 3.81e-05 -> a real difference, in favour of logistic_regression (p < 0.05).
- logistic_regression vs tfidf_logistic_regression: 33 val tickets only logistic_regression got right, 8 only tfidf_logistic_regression got right; p = 0.000178 -> a real difference, in favour of logistic_regression (p < 0.05).

Confusion matrix of the winner (logistic_regression), rows = true, columns = predicted:

| true \ predicted | Account Lockout | CRM | Certificate | Compute | Connectivity | DNS | Disk Space | ERP | Email Delivery | Endpoint Alert | MFA | Monitoring | Performance | Phishing | Printer | Provisioning | Service Outage | Software Installation | Storage Access | Teams | VPN | Wi-Fi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Account Lockout** | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **CRM** | 0 | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **Certificate** | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **Compute** | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **Connectivity** | 0 | 1 | 0 | 0 | 7 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **DNS** | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **Disk Space** | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **ERP** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 7 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| **Email Delivery** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **Endpoint Alert** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **MFA** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **Monitoring** | 0 | 0 | 0 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| **Performance** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **Phishing** | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 1 | 0 | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **Printer** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **Provisioning** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | 0 |
| **Service Outage** | 0 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0 |
| **Software Installation** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 0 |
| **Storage Access** | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 4 | 0 | 0 | 0 |
| **Teams** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 |
| **VPN** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 |
| **Wi-Fi** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 8 |

Most confused pairs (true -> predicted):

1. Endpoint Alert -> Monitoring: 8
2. Monitoring -> DNS: 6
3. Storage Access -> Account Lockout: 3
4. Connectivity -> CRM: 1
5. ERP -> Software Installation: 1

## priority

770 train / 176 val tickets, 4 classes.

| model | accuracy | macro-P | macro-R | macro-F1 | Brier | fit time |
|---|---|---|---|---|---|---|
| logistic_regression **(winner)** | 0.398 | 0.277 | 0.287 | 0.277 | 0.409 | 0.18 s |
| linear_svm_calibrated | 0.432 | 0.263 | 0.291 | 0.268 | 0.295 | 1.07 s |
| random_forest | 0.455 | 0.226 | 0.291 | 0.254 | 0.254 | 1.55 s |
| _ref: majority_ | 0.432 | 0.108 | 0.250 | 0.151 | 0.568 | 0.00 s |
| _ref: tfidf_logistic_regression_ | 0.500 | 0.249 | 0.321 | 0.281 | 0.259 | 0.81 s |

- logistic_regression vs linear_svm_calibrated: 7 val tickets only logistic_regression got right, 13 only linear_svm_calibrated got right; p = 0.263 -> could be chance (p >= 0.05).
- logistic_regression vs tfidf_logistic_regression: 20 val tickets only logistic_regression got right, 38 only tfidf_logistic_regression got right; p = 0.0256 -> a real difference, in favour of tfidf_logistic_regression (p < 0.05).

Confusion matrix of the winner (logistic_regression), rows = true, columns = predicted:

| true \ predicted | P1 | P2 | P3 | P4 |
|---|---|---|---|---|
| **P1** | 0 | 13 | 5 | 3 |
| **P2** | 3 | 31 | 25 | 1 |
| **P3** | 4 | 31 | 36 | 5 |
| **P4** | 0 | 7 | 9 | 3 |

Most confused pairs (true -> predicted):

1. P3 -> P2: 31
2. P2 -> P3: 25
3. P1 -> P2: 13
4. P4 -> P3: 9
5. P4 -> P2: 7
