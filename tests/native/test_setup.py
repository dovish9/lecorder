"""macOS installer orchestration, isolated fake tools; run explicitly with unittest."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='setup test ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root/'bin'; self.bin.mkdir()
        (self.root/'scripts').mkdir()
        shutil.copy2(ROOT/'scripts/setup.sh', self.root/'scripts/setup.sh')
        shutil.copy2(ROOT/'scripts/setup-plan.sh', self.root/'scripts/setup-plan.sh')
        shutil.copy2(ROOT/'scripts/python-runtime.sh', self.root/'scripts/python-runtime.sh')
        shutil.copy2(ROOT/'Brewfile', self.root/'Brewfile')
        (self.root/'requirements.txt').touch()
        self.env = dict(os.environ, PATH=str(self.bin)+':/usr/bin:/bin', FIXTURE=str(self.root))
        self.tool('uname', 'echo Darwin')
        self.tool('xcode-select', 'exit "${CLT_FAIL:-0}"')
        self.tool('xcrun', 'exit "${CLT_FAIL:-0}"')
        self.tool('cmake', 'exit 0')
        self.tool('git', 'echo "git $*" >> "$FIXTURE/log"; [[ "$1" == --version ]]')
        self.tool('brew', '''echo "brew $*" >> "$FIXTURE/log"
if [[ "$1" == install && "${CONSUME_INPUT:-0}" == 1 ]]; then cat >/dev/null; fi
if [[ "$1" == list ]]; then [[ "$3" != "${MISSING:-}" ]]; exit $?; fi
if [[ "$1" == --prefix ]]; then echo "$FIXTURE"; fi''')
        self.tool('python3.13', """echo "executable $0" >> "$FIXTURE/log"
if [[ "$1" == -B ]]; then shift; fi
if [[ "$1" == -c && "$2" == *version_info.major* && "${NO_PYTHON:-0}" == 1 ]]; then exit 1; fi
if [[ "$1" == -c && "$2" == *version_info* && "${BAD_PYTHON:-0}" == 1 ]]; then exit 1; fi
if [[ "$1" == -c && "$2" == *EXTERNALLY-MANAGED* && "${MANAGED:-0}" == 1 ]]; then exit 1; fi
if [[ "$1" == scripts/check-install.py && "${FINAL_FAIL:-0}" == 1 ]]; then exit 1; fi
echo "python $*" >> "$FIXTURE/log"
""")
        shutil.copy2(self.bin/'python3.13', self.bin/'python3')
        (self.root/'libexec/bin').mkdir(parents=True)
        shutil.copy2(self.bin/'python3.13', self.root/'libexec/bin/python3')
        for name in ('whisper','llama','kiwi','vision'):
            (self.root/f'scripts/build-{name}.sh').write_text(f'echo build-{name} >> "$FIXTURE/log"\n[[ "${{BUILD_FAIL:-}}" != {name} ]]\n')

    def tool(self, name, body):
        path = self.bin/name; path.write_text('#!/bin/bash\n'+body+'\n');path.chmod(0o755)

    def run_setup(self, *args, **env):
        return subprocess.run(['/bin/bash', str(self.root/'scripts/setup.sh'), *args], env={**self.env, **env}, capture_output=True, text=True)

    def log(self):
        path=self.root/'log';return path.read_text() if path.exists() else ''

    def test_success_installs_only_missing_and_all_five_stages(self):
        result=self.run_setup(MISSING='cmake')
        self.assertEqual(result.returncode,0,result.stderr)
        for n in range(1,6):self.assertIn(f'[{n}/5]',result.stdout)
        self.assertEqual(self.log().count('brew install'),1)
        self.assertIn('brew install cmake',self.log())
        self.assertNotIn('brew install git', self.log())
        self.assertIn('git --version', self.log())
        self.assertNotIn('upgrade',self.log())
        self.assertIn(f'executable {self.bin}/python3\npython -m pip install', self.log())
        order=[self.log().index(x) for x in ['build-whisper','build-llama','build-kiwi','build-vision','download-models.py','check-install.py\n']]
        self.assertEqual(order,sorted(order))
        (self.root/'log').write_text('')
        result=self.run_setup()
        self.assertEqual(result.returncode,0,result.stderr)

    def test_install_cannot_consume_remaining_formula_names(self):
        with (self.root/'Brewfile').open('a') as stream:
            stream.write('brew "fixture-tool"\n')
        result = self.run_setup(MISSING='cmake', CONSUME_INPUT='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('brew list --versions fixture-tool\n', self.log())

    def test_existing_python_skips_homebrew_python(self):
        result = self.run_setup(MISSING='python')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('brew install python', self.log())
        self.assertNotIn('brew --prefix python', self.log())

    def test_missing_brew_cmake_binary_stops_install(self):
        (self.bin/'cmake').unlink()
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('설치된 실행 파일을 찾을 수 없습니다', result.stderr)
        self.assertNotIn('[3/5]', result.stdout)

    def test_unusable_git_stops_before_install(self):
        self.tool('git', 'exit 1')
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Git이 필요합니다', result.stderr)
        self.assertNotIn('brew install', self.log())

    def test_dry_run_never_installs_builds_or_downloads(self):
        result = self.run_setup('--dry-run', MISSING='cmake')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('설치 예정: cmake', result.stdout)
        for forbidden in ('brew install', 'pip install', 'build-whisper', 'download-models.py'):
            self.assertNotIn(forbidden, self.log())
        self.assertNotIn('설치 완료:', result.stdout)

    def test_invalid_option_stops_before_work(self):
        result = self.run_setup('--typo')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.log(), '')

    def test_prerequisites_stop_before_packages(self):
        for missing in ('clt','brew'):
            with self.subTest(missing=missing):
                if missing=='brew':(self.bin/'brew').unlink()
                result=self.run_setup(CLT_FAIL='1' if missing=='clt' else '0')
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn('[2/5]',result.stdout)

    def test_managed_python_installs_in_project_and_records_runtime(self):
        result = self.run_setup(MANAGED='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        target = self.root/'dependencies/python/site-packages'
        self.assertIn(f'pip install --upgrade --target {target}', self.log())
        self.assertNotIn('break-system-packages', self.log())
        self.assertEqual((self.root/'dependencies/python/runtime').read_text().splitlines(),
                         [str(self.bin/'python3'), str(target)])

    def pyenv_fixture(self, installed=True):
        executable = self.root/'pyenv/versions/3.13.12/bin/python3'
        executable.parent.mkdir(parents=True)
        shutil.copy2(self.bin/'python3.13', executable)
        if installed: (self.root/'pyenv-ready').touch()
        self.tool('pyenv', '''echo "pyenv $*" >> "$FIXTURE/log"
case "$1" in
root) echo "$FIXTURE/pyenv" ;;
versions) if [[ -f "$FIXTURE/pyenv-ready" ]]; then echo 3.13.12; fi ;;
install) touch "$FIXTURE/pyenv-ready" ;;
*) exit 1 ;;
esac''')
        return executable

    def test_pyenv_reuses_installed_313(self):
        executable = self.pyenv_fixture()
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'executable {executable}\npython -m pip install', self.log())
        self.assertNotIn('pyenv install', self.log())
        self.assertNotIn('brew install python', self.log())
        self.assertEqual((self.root/'dependencies/python/runtime').read_text().splitlines()[0], str(executable))

    def test_pyenv_missing_313_preview_then_install(self):
        self.pyenv_fixture(installed=False)
        result = self.run_setup('--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('3.13 설치 예정', result.stdout)
        self.assertFalse((self.root/'pyenv-ready').exists())
        result = self.run_setup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('pyenv install -s 3.13', self.log())
        self.assertNotIn('brew install python', self.log())

    def test_no_python_installs_homebrew_python(self):
        result = self.run_setup(NO_PYTHON='1', MISSING='python', MANAGED='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('brew install python', self.log())
        self.assertEqual((self.root/'dependencies/python/runtime').read_text().splitlines()[0], str(self.root/'libexec/bin/python3'))

    def test_incompatible_python_stops_before_install(self):
        result = self.run_setup(BAD_PYTHON='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Python 3.11', result.stderr)
        self.assertNotIn('pip install', self.log())
        self.assertNotIn('[4/5]', result.stdout)

    def test_failed_build_never_downloads_or_reports_success(self):
        result=self.run_setup(BUILD_FAIL='llama')
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('build-kiwi',self.log())
        self.assertNotIn('download-models.py',self.log())
        self.assertNotIn('설치 완료:',result.stdout)
        self.assertIn('[4/5]',result.stderr)

    def test_failed_final_check_cannot_report_success(self):
        result=self.run_setup(FINAL_FAIL='1')
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('설치 완료:',result.stdout)
        self.assertIn('[5/5]',result.stderr)
