import difflib
import os
import subprocess
import xml.etree.ElementTree as ET

"""
Get all tests from the /tests/ directory, run them, and compare to expected outputs

WARNING: this is super finicky at the moment. if you can find a better way to compare the compiled XML output, 
you'll probably find it incredibly valuable to do so
"""


def elements_equal(e1, e2):
    """source: https://stackoverflow.com/questions/7905380/testing-equivalence-of-xml-etree-elementtree"""
    if e1.tag != e2.tag:
        return False
    if e1.text != e2.text:
        print("found failure", e1, e2)
        return False
    if e1.attrib != e2.attrib:
        return False
    if len(e1) != len(e2):
        return False
    return all(elements_equal(c1, c2) for c1, c2 in zip(e1, e2))


def run_test(test_name: str):
    """Runs the given test"""
    directory = os.path.join(test_directory, test_name)

    infile = os.path.join(directory, "import.xml")
    outfile = os.path.join(directory, "issue.xml")

    subprocess.run(
        ["python", os.path.join(os.getcwd(), "prepress.py"), "v1xxiy", infile],
        stdout=subprocess.DEVNULL,
    )

    generated_output_filepath = os.path.join(os.getcwd(), "issue.xml")

    expected_xml = ET.parse(outfile)
    actual_xml = ET.parse(generated_output_filepath)

    expected_out = ET.tostring(expected_xml.getroot())
    actual_out = ET.tostring(actual_xml.getroot())

    if elements_equal(expected_xml.getroot(), actual_xml.getroot()) == False:
        print(f"\033[91mTest failed: {test_name}\033[0m")

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
