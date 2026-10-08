"""Exercise service cleanup without launching ROS or touching robot processes."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

WRAPPER = Path(__file__).with_name('owned_process.py')


def alive(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
    except FileNotFoundError:
        return False


class OwnedProcessTests(unittest.TestCase):
    def test_exit_code_is_preserved(self):
        result = subprocess.run([sys.executable, str(WRAPPER), str(os.getpid()),
                                 sys.executable, '-c', 'raise SystemExit(7)'],
                                start_new_session=True, timeout=5)
        self.assertEqual(result.returncode, 7)

    def exercise_cleanup(self, kill_owner):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / 'pids'
            child_code = (
                'import os, subprocess, sys, time; '
                'from pathlib import Path; '
                'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
                f'Path({str(pid_file)!r}).write_text(str(os.getpid())+" "+str(p.pid)); '
                'time.sleep(60)')
            owner_code = (
                'import os, subprocess, sys, time; '
                f'p=subprocess.Popen([sys.executable,{str(WRAPPER)!r},str(os.getpid()),'
                f'sys.executable,"-c",{child_code!r}],start_new_session=True); '
                'print(p.pid,flush=True); time.sleep(60)')
            owner = subprocess.Popen([sys.executable, '-c', owner_code],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True)
            wrapper = int(owner.stdout.readline())
            pids = []
            try:
                deadline = time.monotonic() + 5
                while not pid_file.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(pid_file.exists())
                pids = [int(value) for value in pid_file.read_text().split()]
                if kill_owner:
                    owner.kill()
                    owner.wait(timeout=3)
                else:
                    os.killpg(wrapper, signal.SIGINT)
                deadline = time.monotonic() + 5
                while any(alive(pid) for pid in [wrapper, *pids]) and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertFalse(any(alive(pid) for pid in [wrapper, *pids]))
            finally:
                if alive(wrapper):
                    os.killpg(wrapper, signal.SIGKILL)
                owner.kill() if owner.poll() is None else None
                owner.wait(timeout=3)
                owner.stdout.close()

    def test_owner_hard_exit_cleans_children_and_grandchildren(self):
        self.exercise_cleanup(kill_owner=True)

    def test_ctrl_c_cleans_children_and_grandchildren(self):
        self.exercise_cleanup(kill_owner=False)


if __name__ == '__main__':
    unittest.main()
