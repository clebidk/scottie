.PHONY: test deploy install run pull-out review

PROD ?= prod
REMOTE_DIR ?= ~/advertorial
# Fix cycle 3 item 9: default OUT is this operator's Mac scratchpad; override
# with OUT=<path> for a different machine/session.
OUT ?= /private/tmp/claude-501/-Users-calebniednagel-Library-Application-Support-Claude-scratch-workspaces-3f6abd52-3538-42c5-bb69-4f4afa6d4320-e0964356-ffe7-4800-877b-e4aeb9f3427e-scratch-2026-09-08-528fc2/c314de37-7ea4-4fd8-aef7-2fc59e8b623c/scratchpad/review

test:
	python3 -m venv .venv-local
	.venv-local/bin/pip install -q --upgrade pip
	.venv-local/bin/pip install -q pytest jinja2 anthropic python-dotenv
	.venv-local/bin/python -m pytest -q

deploy:
	rsync -az \
		--exclude '.venv*' \
		--exclude 'vendor' \
		--exclude 'models' \
		--exclude 'fixtures' \
		--exclude 'out' \
		--exclude 'runs' \
		--exclude '.env' \
		--exclude '.git' \
		--exclude '__pycache__' \
		--exclude '*.pyc' \
		adv cartridges claims brand tests docs pyproject.toml Makefile \
		$(PROD):$(REMOTE_DIR)/

install:
	ssh $(PROD) 'cd $(REMOTE_DIR) && .venv/bin/pip install -e . && .venv/bin/pip install jinja2'

run:
	ssh $(PROD) 'cd $(REMOTE_DIR) && .venv/bin/adv run $(INPUT)'

pull-out:
	rsync -az $(PROD):$(REMOTE_DIR)/out/ ./out/

# Fix cycle 3 item 9: one-off operator review -- runs `adv review` on the
# server for a given run dir, then rsyncs back only the three generated
# review.html files (never the run's other output) to a local path.
review:
	ssh $(PROD) 'cd $(REMOTE_DIR) && .venv/bin/adv review out/$(RUN)'
	mkdir -p $(OUT)
	rsync -az $(PROD):$(REMOTE_DIR)/out/$(RUN)/*-review.html $(OUT)/
