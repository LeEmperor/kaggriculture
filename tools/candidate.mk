# Shared, deliberately thin policy-local facade. CANDIDATE is exported by Make
# and read as data by candidate.py, so it is never interpolated into a recipe.
PROJECT_ROOT := $(abspath $(POLICY_DIR)/../../..)
MANIFEST := $(POLICY_DIR)/submission.json
PYTHON ?= python3
export CANDIDATE

.PHONY: candidate check

candidate:
	$(PYTHON) $(PROJECT_ROOT)/tools/candidate.py --manifest $(MANIFEST) build

check:
	$(PYTHON) $(PROJECT_ROOT)/tools/candidate.py --manifest $(MANIFEST) check
