-- Oracle schema for the extension value store (see scripts/extension_store.py).
-- Tested layout for Oracle XE 21c (service XEPDB1). Placeholders: change every password,
-- never commit real ones. Run PART 1 as SYSTEM/admin, PART 2 as CICD_OWNER.

-- ===== PART 1 (admin) ======================================================
-- CREATE USER cicd_owner   IDENTIFIED BY "<owner-password>";
-- GRANT CREATE SESSION, CREATE TABLE, CREATE VIEW, CREATE SEQUENCE TO cicd_owner;
-- ALTER USER cicd_owner QUOTA UNLIMITED ON USERS;
-- CREATE USER qa_reader    IDENTIFIED BY "<qa-password>";    -- reads QA values
-- CREATE USER pd_reader    IDENTIFIED BY "<pd-password>";    -- reads PD values
-- CREATE USER plan_reader  IDENTIFIED BY "<plan-password>";  -- metadata only (no values)
-- GRANT CREATE SESSION TO qa_reader, pd_reader, plan_reader;

-- ===== PART 2 (as CICD_OWNER) ==============================================
CREATE TABLE extension_values (
  id             NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  environment    VARCHAR2(10)   NOT NULL CHECK (environment IN ('qa','pd')),
  extension_type VARCHAR2(30)   NOT NULL,
  component_id   VARCHAR2(64)   NOT NULL,
  component_name VARCHAR2(200),
  field_name     VARCHAR2(200)  NOT NULL,
  value_text     VARCHAR2(4000) NOT NULL,
  is_secret      NUMBER(1) DEFAULT 1 NOT NULL CHECK (is_secret IN (0,1)),
  is_active      NUMBER(1) DEFAULT 1 NOT NULL CHECK (is_active IN (0,1)),
  updated_at     TIMESTAMP DEFAULT SYSTIMESTAMP,
  updated_by     VARCHAR2(100),
  CONSTRAINT uq_ext UNIQUE (environment, extension_type, component_id, field_name)
);

-- Full views (with values): one per environment, granted to that environment's reader only.
CREATE VIEW qa_extension_values AS SELECT * FROM extension_values WHERE environment = 'qa' AND is_active = 1;
CREATE VIEW pd_extension_values AS SELECT * FROM extension_values WHERE environment = 'pd' AND is_active = 1;
GRANT SELECT ON qa_extension_values TO qa_reader;
GRANT SELECT ON pd_extension_values TO pd_reader;

-- Metadata views (NO value column): what the ungated "plan" step reads before approval.
CREATE VIEW qa_extension_meta AS
  SELECT environment, extension_type, component_id, component_name, field_name, is_secret
  FROM extension_values WHERE environment = 'qa' AND is_active = 1;
CREATE VIEW pd_extension_meta AS
  SELECT environment, extension_type, component_id, component_name, field_name, is_secret
  FROM extension_values WHERE environment = 'pd' AND is_active = 1;
GRANT SELECT ON qa_extension_meta TO plan_reader;
GRANT SELECT ON pd_extension_meta TO plan_reader;
