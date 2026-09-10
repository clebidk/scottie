.PHONY: test deploy install run review sweep pull-out

# Every target that touches a tenant takes TENANT=<name>; the harness itself
# falls back to tenants/default.txt when it is not given.
TENANT ?= peak-saunas
PROD ?= prod
REMOTE_DIR ?= ~/advertorial
OUT ?= ./review

test:
	python3 -m venv .venv-local
	.venv-local/bin/pip install -q --upgrade pip
	.venv-local/bin/pip install -q -e . pytest
	.venv-local/bin/python -m pytest -q

# INPUT is a local path or a Drive link/id, e.g.
#   make run TENANT=peak-saunas INPUT=tenants/peak-saunas/fixtures/hidden-costs-v2.mov
run:
	ssh $(PROD) 'cd $(REMOTE_DIR) && .venv/bin/harness run $(INPUT) --tenant $(TENANT) $(FLAGS)'

deploy:
	rsync -az \
		--exclude '.venv*' \
		--exclude 'vendor' \
		--exclude 'models' \
		--exclude '.git' \
		--exclude '__pycache__' \
		--exclude '*.pyc' \
		--exclude '*.egg-info' \
		--exclude 'tenants/*/out' \
		--exclude 'tenants/*/runs' \
		--exclude 'tenants/*/.env' \
		--exclude 'tenants/*/fixtures/*.mov' \
		--exclude 'tenants/*/fixtures/*.wav' \
		harness cartridges agents workflows crons evals tenants tests docs \
		pyproject.toml Makefile README.md \
		$(PROD):$(REMOTE_DIR)/

install:
	ssh $(PROD) 'cd $(REMOTE_DIR) && .venv/bin/pip install -e .'

# One-off operator review: builds the self-contained review.html files on the
# server, then pulls back only those files -- never the rest of the run.
review:
	ssh $(PROD) 'cd $(REMOTE_DIR) && .venv/bin/harness review tenants/$(TENANT)/out/$(RUN) --tenant $(TENANT)'
	mkdir -p $(OUT)
	rsync -az $(PROD):$(REMOTE_DIR)/tenants/$(TENANT)/out/$(RUN)/*-review.html $(OUT)/

# Every text fixture the tenant has, one run each. A STOP on one fixture does
# not end the sweep.
sweep:
	ssh $(PROD) 'cd $(REMOTE_DIR) && for f in tenants/$(TENANT)/fixtures/*.txt; do \
		echo "=== $$f ==="; \
		.venv/bin/harness run "$$f" --tenant $(TENANT) $(FLAGS) || echo "FAILED: $$f"; \
	done'

pull-out:
	rsync -az $(PROD):$(REMOTE_DIR)/tenants/$(TENANT)/out/ ./out/$(TENANT)/
