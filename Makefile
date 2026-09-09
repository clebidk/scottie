.PHONY: test deploy install run pull-out

PROD ?= prod
REMOTE_DIR ?= ~/advertorial

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
