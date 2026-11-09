# SPDX-FileCopyrightText: 2026 Jeff C. Jensen
# SPDX-License-Identifier: BSD-3-Clause

VENV := .venv
PYTHON := $(VENV)/bin/python
PRE_COMMIT := $(VENV)/bin/pre-commit
BUN := $(VENV)/bin/bun
BUN_VERSION := $(shell cat .bun-version)
BUN_OS := $(shell uname -s | tr '[:upper:]' '[:lower:]')
BUN_ARCH := $(subst arm64,aarch64,$(subst x86_64,x64,$(shell uname -m)))
BUN_ASSET := bun-$(BUN_OS)-$(BUN_ARCH)
BUN_RELEASE := https://github.com/oven-sh/bun/releases/download/bun-v$(BUN_VERSION)
GALLERY_DIR ?= .agent-context/gallery

# for the tools that recipes start; make 3.81 finds a simple recipe's own command on its original PATH
export PATH := $(CURDIR)/$(VENV)/bin:$(PATH)

.PHONY: config deps lint test test-js test-py coverage-js coverage-py test-live check render gallery

config: deps
	$(PRE_COMMIT) install
	$(PRE_COMMIT) install-hooks

deps: $(BUN)
	$(PYTHON) -m pip install --quiet --upgrade pip
	$(PYTHON) -m pip install --quiet -e . --group dev
	$(BUN) install --frozen-lockfile

$(BUN): .bun-version
	@test -d $(VENV) || python3 -m venv $(VENV)
	@tmp="$$(mktemp -d)"; trap 'rm -rf "$$tmp"' EXIT; \
	curl -fsSL -o "$$tmp/$(BUN_ASSET).zip" "$(BUN_RELEASE)/$(BUN_ASSET).zip" && \
	curl -fsSL -o "$$tmp/SHASUMS256.txt" "$(BUN_RELEASE)/SHASUMS256.txt" && \
	(cd "$$tmp" && grep " $(BUN_ASSET).zip$$" SHASUMS256.txt | shasum -a 256 -c -) && \
	unzip -q -o -j "$$tmp/$(BUN_ASSET).zip" "$(BUN_ASSET)/bun" -d $(VENV)/bin && \
	touch $@

lint:
	$(PRE_COMMIT) run --all-files --show-diff-on-failure --color always

test-js:
	$(BUN) test

test-py:
	$(PYTHON) -m pytest

test: test-js test-py

coverage-js:
	$(BUN) test --coverage-reporter=lcov

coverage-py:
	$(PYTHON) -m pytest --cov=bart_corpus --cov-report=lcov:coverage/python.lcov

test-live:
	tests/live/run.sh

check: lint test

render:
	tools/render_widgets.sh

gallery:
	tools/render_widgets.sh --gallery $(GALLERY_DIR)
