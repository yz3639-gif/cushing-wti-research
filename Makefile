PYTHON ?= .venv/bin/python
export MPLCONFIGDIR := $(CURDIR)/tmp/matplotlib

.PHONY: setup validate build report notebook check test reproduce package
setup:
	bash setup.sh
validate:
	$(PYTHON) -m cushing_research validate --scope public --offline
build:
	$(PYTHON) -m cushing_research build --offline
report:
	$(PYTHON) -m cushing_research report --offline
	$(PYTHON) scripts/build_site.py
notebook:
	$(PYTHON) scripts/run_notebook.py
check:
	$(PYTHON) scripts/check_release.py
test:
	$(PYTHON) -m pytest -q
reproduce:
	$(MAKE) validate build report notebook check test
package:
	$(PYTHON) scripts/package_release.py
