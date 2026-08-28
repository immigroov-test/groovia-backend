-- Read-only. Safe to run even when nothing has been applied: it only reads the system
-- catalog plus tables that have always existed, so a missing table reports MISSING rather
-- than aborting the query.
SELECT 'A. pricing: compute_booking_price anchored on base currency' AS check,
       COALESCE((SELECT CASE WHEN prosrc LIKE '%currency_anchor_country(v_base_ccy)%'
                             THEN 'OK' ELSE 'OLD VERSION' END
                   FROM pg_proc WHERE proname = 'compute_booking_price' LIMIT 1), 'MISSING') AS status
UNION ALL SELECT 'B. pricing: display_service_prices anchored on base currency',
       COALESCE((SELECT CASE WHEN prosrc LIKE '%currency_anchor_country(v_base_ccy)%'
                             THEN 'OK' ELSE 'OLD VERSION' END
                   FROM pg_proc WHERE proname = 'display_service_prices' LIMIT 1), 'MISSING')
UNION ALL SELECT 'C. pricing: reprice trigger installed',
       CASE WHEN EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_reprice_services')
            THEN 'OK' ELSE 'MISSING' END
UNION ALL SELECT 'D. pricing: services matching their mentor base',
       (SELECT CASE WHEN COUNT(*) = 0 THEN 'OK - none mismatched'
                    ELSE COUNT(*)::text || ' STILL MISMATCHED' END
          FROM services s JOIN mentors m ON m.id = s.mentor_id
         WHERE COALESCE(s.set_price,0) > 0
           AND UPPER(COALESCE(s.set_currency,'')) <> UPPER(COALESCE(m.currency,'')))
UNION ALL SELECT 'E. chat: append_chat_messages',
       CASE WHEN EXISTS (SELECT 1 FROM pg_proc WHERE proname='append_chat_messages') THEN 'OK' ELSE 'MISSING' END
UNION ALL SELECT 'F. chat: prune_chat_history',
       CASE WHEN EXISTS (SELECT 1 FROM pg_proc WHERE proname='prune_chat_history') THEN 'OK' ELSE 'MISSING' END
UNION ALL SELECT 'G. consent: consent_events table',
       CASE WHEN to_regclass('consent_events') IS NOT NULL THEN 'OK' ELSE 'MISSING' END
UNION ALL SELECT 'H. legal CMS: legal_documents table',
       CASE WHEN to_regclass('legal_documents') IS NOT NULL THEN 'OK'
            ELSE 'MISSING - run legal_documents_setup.sql then legal_documents_seed_content.sql' END
UNION ALL SELECT 'I. legal CMS: legal_document_versions table',
       CASE WHEN to_regclass('legal_document_versions') IS NOT NULL THEN 'OK' ELSE 'MISSING' END
UNION ALL SELECT 'J. consent flow: legal_consent_events table',
       CASE WHEN to_regclass('legal_consent_events') IS NOT NULL THEN 'OK'
            ELSE 'MISSING - run legal_consent_flow_setup.sql' END
UNION ALL SELECT 'K. consent flow: data_subject_requests table',
       CASE WHEN to_regclass('data_subject_requests') IS NOT NULL THEN 'OK' ELSE 'MISSING' END
UNION ALL SELECT 'L. consent flow: record_legal_consent function',
       CASE WHEN EXISTS (SELECT 1 FROM pg_proc WHERE proname='record_legal_consent') THEN 'OK' ELSE 'MISSING' END
UNION ALL SELECT 'M. legal: is_major on pending updates (mine)',
       COALESCE((SELECT CASE WHEN prosrc LIKE '%is_major%' THEN 'OK' ELSE 'OLD VERSION' END
                   FROM pg_proc WHERE proname='legal_pending_updates' LIMIT 1), 'MISSING')
UNION ALL SELECT 'N. legal: legal_document_recipients (mine)',
       CASE WHEN EXISTS (SELECT 1 FROM pg_proc WHERE proname='legal_document_recipients') THEN 'OK' ELSE 'MISSING' END
ORDER BY 1;
