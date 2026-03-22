### how to install required packages

the recommended way is to use `pipenv`

run `pipenv install` from the root of the project

*note:* it is possible that `pipenv` complains that you do not have the correct python version on your machine

in this case, install `pyenv` first, and then `pipenv` will prompt you to install the correct python version when run

### how to test

first run `pipenv shell` to activate the virtual environment for `pipenv`

then run `python run-tests.py`

or, to run a single test, run

`python prepress.py v1xxiy test-cases/<test-case-name>/import.xml`
