EXTENSION = pg_prompt_jev
DATA = sql/pg_prompt_jev--0.1.0.sql
.PHONY: test

PG_CONFIG ?= pg_config
PGXS := $(shell $(PG_CONFIG) --pgxs)

ifneq ($(wildcard $(PGXS)),)
include $(PGXS)
else
all install:
	@echo "PostgreSQL server development files are required" >&2; exit 1
endif

test:
	python3 test/test_prompt_jev.py
