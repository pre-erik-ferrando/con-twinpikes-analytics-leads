"""Closed domains (definition v3.3, DT.1.1 and Annex A, plus the technical review of v3.2).

Every value stored in a closed-domain column is upper case. The DDL (ddl.py) and the seed loader
validate against these sets; tests/test_domain.py keeps them consistent with the scoring buckets.
"""
from __future__ import annotations

# --- Scope -------------------------------------------------------------------------------------
# The model is ready for five countries; seeds and acceptance are closed for DE, AT and CH only
# (review, "Alcance de construcción"). BE and NL are activated when TWP delivers their rules.
COUNTRIES = ("DE", "AT", "CH", "BE", "NL")
BUILD_COUNTRIES = ("DE", "AT", "CH")
LANGUAGES = ("de", "fr", "it", "nl", "en")   # en only for the prompt and pool signals

PROFILES = ("POOL_SPECIALIST", "POOL_SPA", "HVAC", "MASS_MARKET", "POND")
# Only Pool specialist has rules today; the precheck blocks any other profile without active rules.
DEFAULT_ACTIVE_PROFILES = ("POOL_SPECIALIST",)

# Postal code length per country (digits). NL is 4 digits + 2 letters, handled apart.
POSTAL_CODE_DIGITS = {"DE": 5, "AT": 4, "CH": 4, "BE": 4}

CURRENCY = {"DE": "EUR", "AT": "EUR", "CH": "CHF", "BE": "EUR", "NL": "EUR"}

# --- Configuration -----------------------------------------------------------------------------
AREA_LEVELS = ("STATE", "SUBREGION", "MUNICIPALITY")
SUBREGION_TYPES = ("KREIS", "LANDKREIS", "KREISFREIE_STADT", "BEZIRK", "ARRONDISSEMENT", "COROP")
CATEGORY_CLASSES = ("INCLUDE", "EXCLUDE", "CONDITIONAL")
TITLE_MATCH_TYPES = ("TOKEN", "PREFIX", "SUBSTRING")
TITLE_RULE_CLASSES = ("HARD", "SOFT")
TITLE_REASONS = ("KA_CHAIN", "COMPETITOR", "FLUIDRA", "MANUFACTURER", "ACCOMMODATION", "PUBLIC_POOL",
                 "OUT_OF_PROFILE")
BRAND_OWNERS = ("COMPETITOR", "FLUIDRA")
SHARED_DOMAIN_KINDS = ("SOCIAL", "SITE_BUILDER", "DIRECTORY", "MARKETPLACE", "EMAIL_PROVIDER")
# The plain lists of biz_cfg_word_list (one table for every list of words, DT.2).
WORD_LISTS = ("NAME_STOPWORD", "LEGAL_FORM", "POOL_SIGNAL", "SHARED_DOMAIN", "PAGE_PRIORITY")
MATCH_SIGNALS = ("VAT", "REGISTER", "PHONE", "DOMAIN", "FLUIDRA_ENTITY", "NAME_EXACT", "NAME_FUZZY",
                 "NAME_CONFIRM", "COORDINATES")
HARD_SIGNALS = ("VAT", "REGISTER", "PHONE", "DOMAIN", "FLUIDRA_ENTITY")
SIGNAL_STRENGTHS = ("HARD", "SOFT")
SOURCE_ENTITIES = ("CUSTOMER", "SF_STATUS", "IMPORT_LIST")
SOURCE_TRANSFORMS = ("NONE", "TRIM", "UPPER", "POSTAL_CODE", "PHONE_E164", "VAT", "COUNTRY_NAME_TO_ISO",
                     "DECIMAL_COMMA", "VALUE_MAP")
COORDINATE_PRECISIONS = ("ADDRESS", "POSTAL_CODE")
QUADRANTS = ("ATTACK", "DEFEND", "ACTIVATE", "OPTIMIZE")
EXPORT_TARGETS = ("SALESFORCE",)   # v3.3 DT.8.3: the cockpit loads the same Salesforce file
EXPORT_SOURCE_KINDS = ("FIELD", "CONSTANT", "VALUE_MAP")
EXPORT_FORMATS = ("TEXT", "NUMBER", "DATE", "BOOLEAN_TEXT")
VALUE_TYPES = ("INT", "FLOAT", "STRING", "BOOL", "LIST")

# --- Scoring (DF.8). Points live in biz_cfg_scoring_rule; these are the closed bucket codes. ---------
SCORING_BUCKETS = {
    "COMPANY_TYPE": ("BUILDER_INSTALLER_SHOP", "MAINTENANCE_OR_PREFAB", "WEAK_UNVERIFIED"),
    "BRANDS": ("GROUP_1", "GROUP_2", "GROUP_3", "NONE"),
    "ZONE": ("ATTACK", "ACTIVATE", "DEFEND", "OPTIMIZE", "NONE"),
    "DIGITAL": ("ECOMMERCE", "OWN_WEBSITE", "NONE"),
    "CONTACT": ("PERSON_EMAIL_PHONE", "EMAIL_PHONE", "ANY", "NONE"),
    "REVIEWS": ("ABOVE_P75", "ABOVE_MEDIAN", "ANY", "NONE"),
}
# The order the breakdown is shown in, the cockpit's (DT.7.4).
SCORING_FACTORS = tuple(SCORING_BUCKETS)
RATINGS = ("HOT", "WARM", "COLD")

# --- Web and model -----------------------------------------------------------------------------
CRAWL_STATUSES = ("OK", "PARTIAL_LIMIT", "PARTIAL_ERROR", "NO_WEBSITE", "SOCIAL_ONLY", "ROBOTS_DISALLOWED",
                  "BLOCKED", "UNREACHABLE", "EMPTY")
NO_OWN_WEB_STATUSES = ("NO_WEBSITE", "SOCIAL_ONLY")
RENDER_METHODS = ("HTTP", "BROWSER", "MIXED")
LLM_MODES = ("AUTO", "BATCH", "DIRECT")
VALIDATIONS = ("VALID", "NOT_VALID", "REVIEW")
OFFERINGS = ("PRODUCTS", "SERVICES", "BOTH")
POOL_FOCUSES = ("TOTAL", "PARTIAL")
COMPANY_TYPES = SCORING_BUCKETS["COMPANY_TYPE"]
ECOMMERCE = ("YES", "NO", "UNKNOWN")
VERDICTS = ("SAME", "DIFFERENT", "UNSURE")
VAT_CHECK_STATUSES = ("VALID", "INVALID_FORMAT", "NOT_FOUND", "UNAVAILABLE", "NOT_CHECKED")
REVIEW_REASONS = ("INSUFFICIENT_EVIDENCE", "NO_WEBSITE", "BLOCKED", "UNREACHABLE", "SOCIAL_ONLY", "TECHNICAL")

# --- Decisions ---------------------------------------------------------------------------------
DISCARD_STAGES = ("CLOSED", "SUPPRESSED", "PREVIOUSLY_EXCLUDED", "TITLE_RULE", "CATEGORY_RULE",
                  "DEFAULT_EXCLUDE", "CUSTOMER_MATCH", "NOT_VALID", "HUMAN_EXCLUSION", "SALESFORCE_EXCLUSION")
MATCH_ORIGINS = ("AUTO", "HUMAN", "SALESFORCE")
MATCH_PASSES = ("CAPTURE", "ENRICH", "REMATCH", "MANUAL")
VALIDATION_DECISIONS = ("PENDING", "ACCEPTED", "EXCLUDED")
VALIDATION_REASONS = ("ALREADY_CUSTOMER", "KA_CHAIN", "DUPLICATE", "HOT_TUB_SAUNA_ONLY", "NOT_VALID",
                      "BUSINESS_CLOSED")
APPROVAL_SCOPES = ("LEAD", "REST_OF_BATCH")
# DF.4's six states. Computed in lgn_v_lead, never stored (insert-only model).
LEAD_STATUSES = ("Found", "Excluded by engine", "To review", "Rejected", "Approved", "Published")
EXPORT_STATUSES = ("GENERATED", "UPLOADED", "FAILED")
# Salesforce's monthly status file (v3.3 DT.8.4): the lead's state there and why it was discarded.
SF_STATUSES = ("CONVERTED", "WORKING", "NURTURING", "DISCARDED")
SF_REASONS = ("BUSINESS_CLOSED", "NO_POOL_EQUIPMENT", "ALREADY_CUSTOMER")

# --- Runs (review, DT.9) -----------------------------------------------------------------------
RUN_TYPES = ("CAPTURE", "ENRICH", "REPROCESS_RULES", "SCORE_ONLY", "CUSTOMER_REMATCH", "IMPORT_LIST", "EXPORT")
RUN_STATES = ("REQUESTED", "SNAPSHOT_FROZEN", "AWAITING_COST_APPROVAL", "RUNNING", "AWAITING_BATCHES",
              "COLLECTING", "CLOSED", "FAILED", "CANCELLED")
TERMINAL_RUN_STATES = ("CLOSED", "FAILED", "CANCELLED")
APPROVAL_TYPES = ("COST", "STALE_CUSTOMER_EXTRACT")
CAPTURE_SOURCES = ("SEARCH", "IMPORT")
# What a file loaded into landing.upload_file is (one table for every uploaded file).
UPLOAD_KINDS = ("CUSTOMER_EXTRACT", "SF_STATUS", "IMPORT_LIST")
# An identity event (business_identity_out): a business absorbed by another (ALIAS), that merge undone
# by Sales Ops reopening a DUPLICATE rejection (UNALIAS: the latest of the two counts), or two to be checked.
IDENTITY_KINDS = ("ALIAS", "UNALIAS", "MERGE_SUSPECT")
# One permission per action; country access is a separate dimension (review, DT.9 "Roles").
PERMISSIONS = ("launch_capture", "launch_enrich", "approve_cost", "validate", "export", "upload_customers",
               "upload_call_results")


def check(value, domain: tuple, what: str):
    """Returns value if it is in the domain (None passes); raises ValueError otherwise."""
    if value is not None and value not in domain:
        raise ValueError(f"{what}={value!r} is not one of {', '.join(domain)}")
    return value
