-- Migration v2: extension store for ALL extension types. Run as CICD_OWNER.
-- Safe to run more than once, and safe if you already added some of the columns by hand:
-- every step checks first. Existing rows and the original code keep working.

-- 1. Columns (each added only if missing)
DECLARE
  PROCEDURE add_col(p_name VARCHAR2, p_ddl VARCHAR2) IS
    n NUMBER;
  BEGIN
    SELECT COUNT(*) INTO n FROM user_tab_columns
     WHERE table_name = 'EXTENSION_VALUES' AND column_name = UPPER(p_name);
    IF n = 0 THEN
      EXECUTE IMMEDIATE 'ALTER TABLE extension_values ADD (' || p_name || ' ' || p_ddl || ')';
    END IF;
  END;
BEGIN
  add_col('sub_key',        'VARCHAR2(200)');
  add_col('row_no',         'NUMBER(6)');
  add_col('use_default',    'NUMBER(1) DEFAULT 0 NOT NULL');
  add_col('connector_type', 'VARCHAR2(60)');
  add_col('process_name',   'VARCHAR2(200)');
END;
/

-- 2. use_default must be 0 or 1 (ignored if the check already exists)
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE extension_values ADD CONSTRAINT ck_ext_use_default CHECK (use_default IN (0,1))';
EXCEPTION WHEN OTHERS THEN
  IF SQLCODE NOT IN (-2264, -2260) THEN RAISE; END IF;
END;
/

-- 3. Uniqueness now includes sub_key, row_no and process_name
BEGIN
  EXECUTE IMMEDIATE 'ALTER TABLE extension_values DROP CONSTRAINT uq_ext';
EXCEPTION WHEN OTHERS THEN
  IF SQLCODE <> -2443 THEN RAISE; END IF;   -- -2443: constraint did not exist
END;
/
BEGIN
  EXECUTE IMMEDIATE 'CREATE UNIQUE INDEX uq_ext_v2 ON extension_values '
    || '(environment, extension_type, component_id, NVL(sub_key,''-''), NVL(row_no,0), field_name, NVL(process_name,''-''))';
EXCEPTION WHEN OTHERS THEN
  IF SQLCODE <> -955 THEN RAISE; END IF;    -- -955: index already exists
END;
/

-- 4. Views recreated with the new columns (existing grants are kept)
CREATE OR REPLACE VIEW qa_extension_values AS
  SELECT environment, extension_type, component_id, component_name, field_name, sub_key, row_no, use_default,
         process_name, connector_type, value_text, is_secret
  FROM extension_values WHERE environment = 'qa' AND is_active = 1;
CREATE OR REPLACE VIEW pd_extension_values AS
  SELECT environment, extension_type, component_id, component_name, field_name, sub_key, row_no, use_default,
         process_name, connector_type, value_text, is_secret
  FROM extension_values WHERE environment = 'pd' AND is_active = 1;
CREATE OR REPLACE VIEW qa_extension_meta AS
  SELECT environment, extension_type, component_id, component_name, field_name, sub_key, row_no, use_default,
         process_name, connector_type, is_secret
  FROM extension_values WHERE environment = 'qa' AND is_active = 1;
CREATE OR REPLACE VIEW pd_extension_meta AS
  SELECT environment, extension_type, component_id, component_name, field_name, sub_key, row_no, use_default,
         process_name, connector_type, is_secret
  FROM extension_values WHERE environment = 'pd' AND is_active = 1;

-- Re-assert the read rights (harmless if already granted)
GRANT SELECT ON qa_extension_values TO qa_reader;
GRANT SELECT ON pd_extension_values TO pd_reader;
GRANT SELECT ON qa_extension_meta   TO plan_reader;
GRANT SELECT ON pd_extension_meta   TO plan_reader;

-- Check: should list the columns, then the four views.
SELECT column_name FROM user_tab_columns WHERE table_name = 'EXTENSION_VALUES' ORDER BY column_id;
SELECT view_name FROM user_views ORDER BY view_name;
