"""backtestsys_plugin API surface — services consumed by QD routes + workers.

Submodules:
- common: shared job/queue/DB patterns reused across defense, autoresearch, paper, live
- verdict: HEALTHY/OVERFIT/INCONCLUSIVE logic (Phase 2)
- defense_service: orchestrate WFA/CPCV/DSR (Phase 2)
- serializer: dataclass → JSON conversion (Phase 2+)
- param_space: schema validation for autoresearch (Phase 3)
- autoresearch_service: structural optimizer (Phase 3)
- paper_service: promote-to-paper flow (Phase 4)
- live_service: promote-to-live flow + qualification gate (Phase 5)
"""
