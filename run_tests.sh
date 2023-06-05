#!/bin/bash

reportfolder="coverage"
jsonreport="coverage.json"
virtualenv="coveragevenv"

# create the folder if it doesn't exist
mkdir -p $reportfolder

# create the virtual environment
python -m venv $virtualenv
# activate the virtual environment
source $virtualenv/bin/activate

# install the requirements
pip install --upgrade pip
pip install -r requirements.txt
pip install coverage pdoc3

# run the tests
coverage run --omit="tests/*" -m unittest discover -s tests -p "*test*.py" -v
# generate the coverage report
coverage json -o $reportfolder/$jsonreport
# generate the html report
coverage html -d $reportfolder/html

# deactivate the virtual environment
deactivate
# remove the virtual environment
rm -rf $virtualenv