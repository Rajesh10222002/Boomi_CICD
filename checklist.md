Applied automatically (scripts/validate_checklist.py) to the process being promoted AND to every process it links to — sub-processes, and sub-processes of sub-processes.

Structure & Naming
Process name follows agreed naming convention e.g. PR_LOAD_[Domain]_[Object]_[Direction]_[Target]
All shapes are labelled clearly — no unnamed shapes (shapes that reference a named component, e.g. a map or connector, count as named)
Sub-processes used appropriately for reusable logic
All linked sub-processes (at any depth) are deployed in the target environment
Process notes / description field is populated
No test data or test-only shapes left in the process
Connectors & Connections
Credentials stored in connection extensions, not hardcoded
Data Handling & Mapping
Scripting in maps is minimal and well-commented
Document properties / dynamic properties used correctly, not overloaded
Error Handling
Try/Catch or error handling paths configured to capture all types of errors, not just document-level errors
Error notifications are configured (email / Slack webhook)
Failed documents are written to a dead-letter path or DB for reprocessing
Retry logic implemented for transient / timeout failures
