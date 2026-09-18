PLUGIN_ID := io.github.samueljenkinsml.battlestation
DEST := $(HOME)/.config/omarchy/plugins/$(PLUGIN_ID)
QMLLINT ?= /usr/lib/qt6/bin/qmllint

.PHONY: dev check test lint validate logs undev

# Copy (not symlink: the validator rejects symlinks) into the shell's plugin
# directory. The shell hot-reloads plugin code on save.
dev:
	mkdir -p $(DEST)
	rsync -a --delete --exclude .git --exclude tests --exclude '__pycache__' --exclude '*.pyc' ./ $(DEST)/
	@echo "synced to $(DEST)"

test:
	python3 -m unittest discover -s tests

lint:
	@for f in *.qml qml/*.qml; do $(QMLLINT) -I /usr/lib/qt6/qml $$f 2>&1 | grep -E "Error|error:" | grep -vE "qs\.|import|not found|Could not" || true; done
	python3 -m py_compile bin/battlestation lib/battlestation/*.py

validate:
	omarchy-plugin-validate .

check: test lint validate

logs:
	qs log -p /usr/share/omarchy/shell 2>/dev/null | grep -iE "battlestation|$(PLUGIN_ID)" | tail -50

undev:
	omarchy plugin remove $(PLUGIN_ID) --yes || rm -rf $(DEST)
