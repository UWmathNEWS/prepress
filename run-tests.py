import os
import subprocess
import xml.etree.ElementTree as ET

"""
Get all tests from the /tests/ directory, run them, and compare to expected outputs

WARNING: this is super finicky at the moment. if you can find a better way to compare the compiled XML output, 
you'll probably find it incredibly valuable to do so
"""


def print_difference(exp, act):
    print(f"\033[91mUnexpected difference found. Something in the below two lines of output is different:\033[0m")
    print(f"\033[91mExpected:\033[0m \"{exp}\"")
    print(f"\033[91mActual:\033[0m \"{act}\"")


def elements_equal(exp, act):
    """source: https://stackoverflow.com/questions/7905380/testing-equivalence-of-xml-etree-elementtree"""
    if exp.tag != act.tag:
        print_difference(exp.tag, act.tag)
        return False
    if exp.text != act.text:
        print_difference(exp.text, act.text)
        return False
    if (exp.tail or "").strip() != (act.tail or "").strip():
        print_difference((exp.tail or "").strip(), (act.tail or "").strip())
        return False
    if exp.attrib != act.attrib:
        print_difference(exp.attrib, act.attrib)
        return False
    if len(exp) != len(act):
        return False
    return all(elements_equal(c1, c2) for c1, c2 in zip(exp, act))


def run_test(test_name: str):
    """Runs the given test"""
    print(f"Running test: {test_name}")

    directory = os.path.join(test_directory, test_name)

    infile = os.path.join(directory, "import.xml")
    expected_output_filepath = os.path.join(directory, "issue.xml")

    prepress_result = subprocess.run(
        ["python", os.path.join(os.getcwd(), "prepress.py"), "v1xxiy", infile],
        stdout=subprocess.DEVNULL,
    )

    if prepress_result.returncode != 0:
        print(f"\033[91mprepress failed to run test: {test_name}\033[0m")
        print(
            "There is likely debug output above. If not, try running the test file directly"
        )
        exit()

    generated_output_filepath = os.path.join(os.getcwd(), "issue.xml")

    expected_xml = ET.parse(expected_output_filepath)
    actual_xml = ET.parse(generated_output_filepath)

    expected_out = ET.tostring(expected_xml.getroot())
    actual_out = ET.tostring(actual_xml.getroot())

    if elements_equal(expected_xml.getroot(), actual_xml.getroot()) == False:
        print(f"\n\033[91mTest failed: {test_name}\033[0m")
        print(f"\n\033[91mGranular error data should be logged above this message.\033[0m")
        print(f"\033[91mMore data below is included to help you to debug the issue.\033[0m\n")

        e_lines = expected_out.splitlines()
        a_lines = actual_out.splitlines()

        if len(e_lines) != len(a_lines):
            if len(e_lines) > len(a_lines):
                print("Expected more lines of output than given.")
            elif len(e_lines) < len(a_lines):
                print("Expected less lines of output than given.")
            print(f"\033[91mExpected\033[0m")
            print(expected_out)
            print(f"\033[91mActual\033[0m")
            print(actual_out)
            exit()

        for i in range(len(e_lines)):
            if e_lines[i] != a_lines[i]:
                print(f"\033[91mUnexpected difference on line {i}:\033[0m")
                print(f"\033[91mExpected:\033[0m")
                print(e_lines[i])
                print(f"\033[91mActual:\033[0m")
                print(a_lines[i])

        exit()


test_directory = os.path.join(os.getcwd(), "test-cases")

test_suites = [
    d
    for d in os.listdir(test_directory)
    if os.path.isdir(os.path.join(test_directory, d))
]

for directory in test_suites:
    run_test(directory)

print("\033[92mAll tests passed :)\033[0m")
