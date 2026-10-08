Structure & Naming
Process name follows agreed naming conventione.g. PR_LOAD_[Domain]_[Object]_[Direction]_[Target] 
All shapes are labelled clearly — no unnamed connectors or maps
Sub-processes used appropriately for reusable logic
Process notes / description field is populated
Connectors & Connections
Connection components are shared / reused — no duplicate connections created
Credentials stored in connection extensions, not hardcoded
Connector operations should not be reused across unrelated components or integration contexts
Data Handling & Mapping
Maps are clean — no orphaned source/destination fields
Scripting in maps is minimal and well-commented
Document properties / dynamic properties used correctly, not overloaded
No sensitive data written to process logs or document cache unnecessarily
Error Handling
Try/Catch or error handling paths configured to capture all types of errors, not just document-level errors
Error notifications are configured (email / Slack webhook)
Failed documents are written to a dead-letter path or DB for reprocessing
Retry logic implemented for transient / timeout failures
Error messages include sufficient contextA generic error description followed by field name, source/target system, and directory info where applicable
Performance & Scalability
Batch processing used where applicable — no unnecessary single-document loops
Document caching used appropriately — no stale or uncleared caches
Scheduled processes do not overlap — execution window is realistic
Testing Evidence
Process tested in Dev with representative data
Happy path and error path both tested
Test execution logs / screenshots attached or referenced
No test data or test-only shapes left in the process