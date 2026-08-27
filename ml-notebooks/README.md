# ml-notebooks

Empty for now. Phase 3 will add training notebooks here for:

- Anomaly detection (unsupervised, residual-based) → feeds `backend/app/ml/anomaly_detector.py`
- Fault classification (supervised, multi-class over the fault table in `docs/physics-model.md`) → `backend/app/ml/fault_classifier.py`
- Remaining-useful-life (RUL) regression → `backend/app/ml/rul_predictor.py`
- Mission-reliability scoring model → `backend/app/ml/mission_reliability.py`

Training data will come from the Phase 2 physics simulation (`backend/app/physics/*`)
run across many synthetic fault scenarios, not from Phase 1's mock generator.
