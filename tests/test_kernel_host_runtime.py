"""Regressions for the actual Clang loader failure, before any firmware download."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kernel_runtime", ROOT / "scripts/internal/check_kernel_host_runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class KernelHostRuntimeTests(unittest.TestCase):
    def test_missing_cpp_stops_with_the_exact_failed_soname(self):
        def loader(name):
            if name == "libc++.so.1":
                raise OSError("cannot open shared object file: No such file or directory")
            return object()
        with self.assertRaisesRegex(RuntimeError, r"libc\+\+\.so\.1:.*cannot open"):
            runtime.check(loader, lambda: "Linux")

    def test_present_library_with_missing_transitive_dependency_is_rejected(self):
        def loader(name):
            raise OSError("libunwind.so.1: cannot open shared object file")
        with self.assertRaisesRegex(RuntimeError, "libunwind.so.1"):
            runtime.check(loader, lambda: "Linux")

    def test_both_native_handles_are_retained_on_success(self):
        handles = {"libc++abi.so.1": object(), "libc++.so.1": object()}
        self.assertEqual(runtime.check(handles.__getitem__, lambda: "Linux"), list(handles.values()))

    def test_windows_is_not_reported_as_a_valid_linux_compiler_test(self):
        with self.assertRaisesRegex(RuntimeError, "Linux build host"):
            runtime.check(lambda name: self.fail("must not load Linux libraries on Windows"), lambda: "Windows")

    def test_both_jobs_check_runtime_before_expensive_work(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        self.assertLess(workflow.index("run: python3 scripts/internal/check_kernel_host_runtime.py"),
                        workflow.index("- name: Download source and target firmware"))
        kernel = (ROOT / ".github/workflows/r8s-chargemax-kernel.yml").read_text()
        self.assertLess(kernel.index("run: python3 scripts/internal/check_kernel_host_runtime.py"),
                        kernel.index("- name: Build pinned r8s kernel"))


if __name__ == "__main__":
    unittest.main()
