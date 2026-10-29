# aprs2.net backend

PYTHON ?= python3

.PHONY: test deps-test

# Run the poller and DNS driver test suite
test:
	cd poller && $(PYTHON) -m pytest tests

# Install the Python packages needed to run the tests
deps-test:
	$(PYTHON) -m pip install -r poller/requirements-test.txt
