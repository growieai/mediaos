.PHONY: setup up migrate seed test check dev acceptance down
setup up migrate seed test check dev acceptance down:
	python scripts/dev.py $@
