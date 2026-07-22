PYTHON ?= .venv/bin/python
PYTEST_ENV = PYTHONPATH=python SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy PYGAME_HIDE_SUPPORT_PROMPT=1

.PHONY: venv install-dev run web simulate test test-web test-all

venv:
	python3 -m venv .venv

install-dev:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements-dev.txt

run:
	PYTHONPATH=python PYGAME_HIDE_SUPPORT_PROMPT=1 $(PYTHON) python/main.py

web:
	PYTHONPATH=python PYGAME_HIDE_SUPPORT_PROMPT=1 $(PYTHON) python/web_main.py

simulate:
	PYTHONPATH=python PYGAME_HIDE_SUPPORT_PROMPT=1 $(PYTHON) python/simulate.py

test:
	$(PYTEST_ENV) $(PYTHON) -m pytest tests

test-web:
	node --test tests/web_board_animations.test.mjs

test-all: test test-web
