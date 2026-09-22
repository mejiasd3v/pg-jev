EXTENSION = pg_prompt_jev
DATA = sql/pg_prompt_jev--0.1.0.sql \
	sql/pg_prompt_jev--0.1.1.sql \
	sql/pg_prompt_jev--0.1.0--0.1.1.sql
.PHONY: test integration benchmark generate

PG_CONFIG ?= pg_config
PGXS := $(shell $(PG_CONFIG) --pgxs)

ifneq ($(wildcard $(PGXS)),)
include $(PGXS)
else
all install:
	@echo "PostgreSQL server development files are required" >&2; exit 1
endif

generate:
	python3 tools/generate_sql.py

test:
	python3 tools/generate_sql.py --check
	python3 -m unittest discover -s test/unit -v
	python3 -m unittest discover -s test/integration -p 'test_*.py' -v

integration:
	python3 test/integration/run.py

benchmark:
	python3 bench/run.py
