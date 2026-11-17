"""The documented normalisation contract for raw ITSM exports (docs/DATA-CARD.md "RAW schema").

Single source of truth: the ingestion pipeline (app/ingest/pipeline.py) uses it to clean records, and the
synthetic data generator (scripts/datagen/raw.py) uses it to write them. Every "recoverable" raw defect can be
fixed with these tables alone.
"""
from __future__ import annotations

PRIORITY_ALIASES = {
    "P1": ["P1", "p1", "1", "1 - Critical", "Critical", "CRITICAL", "Sev-1", "SEV1", "Sev 1", "Urgent"],
    "P2": ["P2", "p2", "2", "2 - High", "High", "HIGH", "Sev-2", "SEV2"],
    "P3": ["P3", "p3", "3", "3 - Moderate", "Medium", "Moderate", "Sev-3", "Normal"],
    "P4": ["P4", "p4", "4", "4 - Low", "Low", "low", "Sev-4", "Planning"],
}
STATUS_ALIASES = {
    "open": ["open", "Open", "New", "NEW", "Logged"],
    "in_progress": ["in_progress", "In Progress", "WIP", "Work in Progress", "Assigned"],
    "resolved": ["resolved", "Resolved", "RESOLVED", "Fixed", "Resolved - Fixed"],
    "closed": ["closed", "Closed", "Closed - Complete", "CLOSED", "Done"],
}
CHANNEL_ALIASES = {
    "portal": ["portal", "Self-Service Portal", "web", "Web Form"],
    "email": ["email", "E-mail", "Email", "mail"],
    "chat": ["chat", "Teams Chat", "Live Chat"],
    "phone": ["phone", "Phone", "Tel", "Call"],
    "monitoring": ["monitoring", "Monitoring", "Auto-Alert", "event"],
}
ENV_ALIASES = {
    "production": ["production", "prod", "PROD", "Prd", "Production"],
    "staging": ["staging", "stg", "STAGE", "Staging"],
    "development": ["development", "dev", "DEV", "Dev"],
}
CATEGORY_ALIASES = {
    "Identity & Access": ["IAM", "Identity and Access", "identity & access", "IDENTITY & ACCESS", "Id & Access"],
    "Network": ["network", "NETWORK", "Netwrk", "Networking"],
    "Collaboration": ["collab", "Collaboration Tools", "COLLABORATION"],
    "Endpoint": ["endpoint", "End User Computing", "EUC", "ENDPOINT"],
    "Cloud": ["cloud", "CLOUD", "Cloud Services"],
    "Database": ["DB", "database", "Databases"],
    "Business Applications": ["Business Apps", "bus. apps", "BUSINESS APPLICATIONS"],
    "Security": ["security", "SecOps", "Infosec"],
    "Infrastructure": ["infra", "Infrastructure Services", "INFRA"],
}
SUBCATEGORY_ALIASES = {"Wi-Fi": ["wifi", "WiFi", "Wireless"], "MFA": ["mfa", "2FA", "Multi-factor"],
                       "Account Lockout": ["lockout", "Acct Lockout"], "Email Delivery": ["mail delivery", "E-mail"],
                       "Software Installation": ["SW Install", "software install"], "Service Outage": ["Outage"]}
TEAM_ALIASES = {"Network Operations": ["NetOps", "Network Ops"], "Site Reliability": ["SRE"],
                "Security Operations": ["SOC", "SecOps Team"], "Service Desk": ["Helpdesk", "SD L1"],
                "Database Operations": ["DBA Team", "DB Ops"], "Identity Operations": ["IdOps"],
                "IAM Engineering": ["IAM Eng"], "Endpoint Support": ["EUC Support"], "Workplace IT": ["Deskside"],
                "Messaging Operations": ["Messaging Ops"], "Collaboration Support": ["Collab Support"],
                "Cloud Platform": ["Cloud Ops"], "ERP Support": ["SAP Support"], "CRM Support": ["Salesforce Support"],
                "Platform Operations": ["Platform Ops"], "Observability": ["Monitoring Team"]}
# Source timestamp conventions (how to parse each system's export).
TS_CONVENTION = {
    "ServiceNow-EMEA": "YYYY-MM-DD HH:MM:SS, UTC, no zone written",
    "Jira-SM": "ISO 8601 with milliseconds and numeric offset, e.g. 2026-06-10T21:15:00.000+0530",
    "Email-Gateway": "RFC 2822 with the sender's numeric offset, e.g. Wed, 10 Jun 2026 10:45:00 -0500",
    "Monitoring-Bridge": "Unix epoch seconds (UTC)",
    "Legacy-Remedy": "US MM/DD/YYYY hh:mm:ss AM/PM at fixed UTC-06:00 (no DST)",
}
MATRIX = {"P1": [(1, 1)], "P2": [(1, 2), (2, 1)], "P3": [(2, 2), (1, 3), (3, 1)], "P4": [(2, 3), (3, 2), (3, 3)]}
LEVEL = {1: "1 - High", 2: "2 - Medium", 3: "3 - Low"}

# The form of each source system's own ticket number (source_ticket_ref). A record whose number does not have
# its own system's form is the suspect one when two records share a number (app/ingest/pipeline.py).
REF_FORMAT = {
    "ServiceNow-EMEA": r"INC\d{7}",
    "Jira-SM": r"ITSD-\d{4,5}",
    "Email-Gateway": r"MSG-[0-9a-f]{8}",
    "Monitoring-Bridge": r"EVT-\d{8}",
    "Legacy-Remedy": r"HD\d{12}",
}
