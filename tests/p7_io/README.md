# Practice 7 / Task 3 raw-byte regression

`ok/p7_print_raw_bytes.txt` deliberately contains non-UTF-8 byte `0xff`
and an embedded `0x00` inside a string literal. Its `.expected` file
contains those exact bytes. The old checker is now byte-safe, and
`tests/check_p7_io.py` validates this case directly with the available
runtime backend(s). The normal `tests/ok` tests are UTF-8 text and are
exercised by both `run_tests.py` and `check.py`.

`tests/ok/NAME.in` supplies stdin for a matching `NAME.txt`; both runners
compare the complete stdout including the final exit line and all newlines.
