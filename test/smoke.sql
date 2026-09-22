CREATE EXTENSION plpython3u;
CREATE EXTENSION pg_prompt_jev;

SELECT prompt_jev(NULL, 'unused') IS NULL AS null_short_circuit;

DO $do$
BEGIN
  PERFORM prompt_jev('text', 'Pick', choice => '["same", "same"]'::jsonb);
  RAISE EXCEPTION 'expected validation error';
EXCEPTION WHEN OTHERS THEN
  IF SQLERRM NOT LIKE '%labels must be unique%' THEN
    RAISE;
  END IF;
END
$do$;
