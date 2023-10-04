# Makefile for wls-analytics
# uses version from git with commit hash

help:
	@echo "make <target>"
	@echo "build	build wls-anylitics."
	@echo "clean	clean all temporary directories."
	@echo ""

build:
	python setup.py bdist_wheel
	rm -fr build	

check:
	pylint wls_analytics 

clean:
	rm -fr build
	rm -fr dist
	rm -fr *.egg-info
	rm -fr *.dist-info


