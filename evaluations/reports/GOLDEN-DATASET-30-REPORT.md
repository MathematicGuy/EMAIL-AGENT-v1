# Golden 30-Email Dataset Evaluation Report

**Date:** 2026-08-25 10:38:42 UTC
**Pass Rate:** 30/30 (100.0%)
**Execution Time:** 0.009s

## Summary Table

| # | Case ID | Category | Expected Intent | Exp | Act | Quarantined | Status |
|---|---|---|---|---|---|---|---|
| 01 | `email_01` | `legal_rag` | `RETRIEVE_RAG` | `clean` | `clean` | `False` | ✅ PASS |
| 02 | `email_02` | `admin_rag` | `RETRIEVE_RAG` | `clean` | `clean` | `False` | ✅ PASS |
| 03 | `email_03` | `citizen_rag` | `RETRIEVE_RAG` | `clean` | `clean` | `False` | ✅ PASS |
| 04 | `email_04` | `education_rag` | `RETRIEVE_RAG` | `clean` | `clean` | `False` | ✅ PASS |
| 05 | `email_05` | `citizen_rag` | `RETRIEVE_RAG` | `clean` | `clean` | `False` | ✅ PASS |
| 06 | `email_06` | `tax_insurance_rag` | `RETRIEVE_RAG` | `clean` | `clean` | `False` | ✅ PASS |
| 07 | `email_07` | `vehicle_rag` | `RETRIEVE_RAG` | `clean` | `clean` | `False` | ✅ PASS |
| 08 | `email_08` | `internal_task` | `DIRECT_PLAN` | `clean` | `clean` | `False` | ✅ PASS |
| 09 | `email_09` | `informational` | `NO_ACTION` | `clean` | `clean` | `False` | ✅ PASS |
| 10 | `email_10` | `marketing_spam` | `NO_ACTION` | `clean` | `clean` | `False` | ✅ PASS |
| 11 | `email_11` | `security_phishing` | `QUARANTINE` | `malicious` | `malicious` | `True` | ✅ PASS |
| 12 | `email_12` | `security_malware_attachment` | `QUARANTINE` | `malicious` | `malicious` | `True` | ✅ PASS |
| 13 | `email_13` | `security_eicar_test` | `QUARANTINE` | `malicious` | `malicious` | `True` | ✅ PASS |
| 14 | `email_14` | `security_prompt_injection` | `QUARANTINE` | `suspicious` | `suspicious` | `True` | ✅ PASS |
| 15 | `email_15` | `security_ssrf_shortener` | `QUARANTINE` | `blocked` | `blocked` | `True` | ✅ PASS |
| 16 | `email_16` | `traffic_law_rag` | `RETRIEVE_RAG` | `clean` | `clean` | `False` | ✅ PASS |
| 17 | `email_17` | `labor_law_rag` | `RETRIEVE_RAG` | `clean` | `clean` | `False` | ✅ PASS |
| 18 | `email_18` | `corporate_law_rag` | `RETRIEVE_RAG` | `clean` | `clean` | `False` | ✅ PASS |
| 19 | `email_19` | `technical_ml_rag` | `RETRIEVE_RAG` | `clean` | `clean` | `False` | ✅ PASS |
| 20 | `email_20` | `internal_task_attachment` | `DIRECT_PLAN` | `clean` | `clean` | `False` | ✅ PASS |
| 21 | `email_21` | `internal_task` | `DIRECT_PLAN` | `clean` | `clean` | `False` | ✅ PASS |
| 22 | `email_22` | `security_otp` | `NO_ACTION` | `clean` | `clean` | `False` | ✅ PASS |
| 23 | `email_23` | `newsletter` | `NO_ACTION` | `clean` | `clean` | `False` | ✅ PASS |
| 24 | `email_24` | `security_macro_excel` | `QUARANTINE` | `malicious` | `malicious` | `True` | ✅ PASS |
| 25 | `email_25` | `security_xss_scheme` | `QUARANTINE` | `blocked` | `blocked` | `True` | ✅ PASS |
| 26 | `email_26` | `security_zip_bomb` | `QUARANTINE` | `blocked` | `blocked` | `True` | ✅ PASS |
| 27 | `email_27` | `security_masquerading_pe` | `QUARANTINE` | `malicious` | `malicious` | `True` | ✅ PASS |
| 28 | `email_28` | `security_cloud_malware_hash` | `QUARANTINE` | `malicious` | `malicious` | `True` | ✅ PASS |
| 29 | `email_29` | `clean_multi_attachment` | `DIRECT_PLAN` | `clean` | `clean` | `False` | ✅ PASS |
| 30 | `email_30` | `security_mixed_attachment` | `QUARANTINE` | `malicious` | `malicious` | `True` | ✅ PASS |
