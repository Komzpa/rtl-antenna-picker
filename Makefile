.PHONY: test pycompile

test: pycompile
	python3 -m pytest -q

pycompile:
	python3 -m py_compile rtl_antenna_picker.py
