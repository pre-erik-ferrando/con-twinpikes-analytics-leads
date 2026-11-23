"""One module per run type (review, DT.9): capture (CAPTURE, IMPORT_LIST), enrich (ENRICH), export
(EXPORT and its upload confirmation), maintenance (REPROCESS_RULES, CUSTOMER_REMATCH,
validation helpers). `common` holds what they share: filter, match and score of a set of businesses.
"""
