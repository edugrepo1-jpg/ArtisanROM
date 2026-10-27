"""Host contracts for the optional r8s policy; these do not certify charging hardware."""
import os
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH_DIR = ROOT / 'platform/exynos990/patches/artisankrnl'
PATCHES = ['r8s-fixed-pdo-selection.patch', 'r8s-chargemax-opt-in.patch']
INPUTS = ['drivers/battery_v2/' + p for p in [
    'sec_battery.c', 'sec_battery_sysfs.c', 'sec_step_charging.c',
    'include/sec_battery.h', 'include/sec_battery_sysfs.h']]


def function(source, name):
    start = re.search(r'^[\w \t*]+\b' + name + r'\s*\([^;{}]*\)\s*\{', source, re.M)
    if not start:
        raise AssertionError('Missing definition: ' + name)
    end = re.search(r'^}\s*$', source[start.end():], re.M)
    if not end:
        raise AssertionError('Missing function end: ' + name)
    return source[start.start():start.end() + end.end()]


class ChargingPatchTests(unittest.TestCase):
    def test_dt_patch_preserves_mixed_line_endings_through_git_storage(self):
        # Exact bpf111 r8s.cfg is CRLF while build.sh is LF. Testing only
        # the working copy missed Git's former LF normalization on upload.
        patch = PATCH_DIR / 'r8s-dt-table-revisions.patch'
        data = patch.read_bytes()
        relative = patch.relative_to(ROOT).as_posix()
        result = subprocess.run(['git', 'hash-object', '-w', '--path=' + relative, '--stdin'],
                                input=data, cwd=ROOT, capture_output=True, check=True)
        stored = subprocess.check_output(['git', 'cat-file', 'blob', result.stdout.decode().strip()], cwd=ROOT)
        self.assertEqual(stored, data, 'Git normalized the CRLF patch context')
        self.assertEqual(result.stdout.decode().strip(),
                         hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest())
        fixture = ('exynos990-r8s_eur_open_00.dtbo\n  custom0=0x00000000\n  custom1=0x01000000\n\n'
                   'exynos990-r8s_eur_open_02.dtbo\n  custom0=0x02000000\n  custom1=0x04000000\n\n'
                   'exynos990-r8s_eur_open_05.dtbo\n  custom0=0x05000000\n  custom1=0xff000000\n').replace('\n', '\r\n').encode()
        with tempfile.TemporaryDirectory(prefix='r8s-dt-committed-') as directory:
            stage = Path(directory)
            cfg = stage / 'build/dtconfigs/r8s.cfg'
            cfg.parent.mkdir(parents=True)
            cfg.write_bytes(fixture)
            first_hunk = stored.split(b'diff --git a/build.sh', 1)[0]
            change = stage / 'dt.patch'
            change.write_bytes(first_hunk)
            subprocess.run(['git', '-c', 'core.autocrlf=false', 'apply', str(change)],
                           cwd=stage, check=True, capture_output=True)
            expected = fixture.replace(b'custom1=0x01000000', b'custom1=0x00000001').replace(
                b'custom0=0x02000000', b'custom0=0x00000002').replace(
                b'custom1=0x04000000', b'custom1=0x00000004').replace(
                b'custom0=0x05000000', b'custom0=0x00000005').replace(
                b'custom1=0xff000000', b'custom1=0x000000ff')
            self.assertEqual(cfg.read_bytes(), expected)

    def test_patch_scope_and_preserved_limits(self):
        for patch in PATCHES:
            source = (PATCH_DIR / patch).read_text()
            paths = re.findall(r'^diff --git a/(.*?) b/', source, re.M)
            self.assertTrue(paths)
            self.assertTrue(set(paths).issubset(INPUTS))
            result = subprocess.run(['git', '-c', 'core.autocrlf=false', 'apply', '--numstat', str(PATCH_DIR / patch)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        source = (PATCH_DIR / PATCHES[1]).read_text()
        self.assertIn('+\tbattery->charge_max_mode = false;', source)
        self.assertIn('kstrtoint(buf, 10, &x)', source)
        self.assertIn('if (x != 0 && x != 1)', source)
        changed = '\n'.join(line for line in source.splitlines()
                            if line.startswith(('+', '-')) and not line.startswith(('+++', '---')))
        self.assertNotRegex(changed, r'->(?:temperature|dchg_temp|chg_temp)\s*=')
        self.assertNotRegex(changed, r'->(?:siop_level|chg_limit|mix_limit)\s*=\s*(?:100|false|0)')
        self.assertNotIn('swelling_high_block =', changed)

    def test_only_r8s_selects_the_patches_after_kernel_sync(self):
        source = (PATCH_DIR / 'customize.sh').read_text()
        start = source.index('    if [[ "$TARGET_CODENAME" == "r8s" ]]; then')
        end = source.index('\n    fi', start) + len('\n    fi')
        branch = source[start:end]
        self.assertLess(source.index('git reset --hard FETCH_HEAD'), start)
        self.assertLess(end, source.index('    BUILD_KERNEL ||'))
        shell = os.environ.get('BASH_TEST_EXECUTABLE', 'bash')
        for target in ('r8s', 'y2s', 'p3s'):
            env = dict(os.environ, TARGET_CODENAME=target, SRC_DIR='/source', KERNEL_TMP_DIR='/kernel')
            result = subprocess.run([shell, '-c', 'set -euo pipefail\n'
                                     'APPLY_PATCH_ONCE() { printf "%s\\n" "$2"; }\n' + branch],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for patch in PATCHES:
                self.assertEqual(patch in result.stdout, target == 'r8s')


@unittest.skipUnless(os.environ.get('R8S_CHARGING_KERNEL_DIR'),
                     'Exact cached kernel inputs not configured')
class ExactChargingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='r8s-charging-')
        cls.directory = Path(cls.temporary.name)
        cls.original = {}
        for path in INPUTS:
            data = (Path(os.environ['R8S_CHARGING_KERNEL_DIR']) / path).read_bytes()
            cls.original[path] = data
            destination = cls.directory / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        for patch in PATCHES:
            subprocess.run(['git', '-c', 'core.autocrlf=false', 'apply', '--check', str(PATCH_DIR / patch)],
                           cwd=cls.directory, check=True, capture_output=True)
            subprocess.run(['git', '-c', 'core.autocrlf=false', 'apply', str(PATCH_DIR / patch)],
                           cwd=cls.directory, check=True, capture_output=True)
        cls.source = (cls.directory / INPUTS[0]).read_text()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_raw_siop_setter_and_wireless_policy_remain_original(self):
        path = 'drivers/battery_v2/sec_battery_sysfs.c'
        old = function(self.original[path].decode(), 'sec_bat_store_attrs')
        new = function((self.directory / path).read_text(), 'sec_bat_store_attrs')
        def case(source, name, next_name):
            return source.split('\tcase ' + name + ':', 1)[1].split('\tcase ' + next_name + ':', 1)[0]
        self.assertEqual(case(old, 'SIOP_LEVEL', 'SIOP_EVENT'), case(new, 'SIOP_LEVEL', 'SIOP_EVENT'))
        old = self.original[INPUTS[0]].decode()
        for name in ('sec_bat_get_wireless_current', 'sec_bat_check_lrp_temp',
                     'sec_bat_set_charge'):
            self.assertEqual(function(old, name), function(self.source, name))

    def test_switch_refreshes_current_and_the_regular_monitor_like_archived_v3(self):
        source = (self.directory / INPUTS[1]).read_text()
        setter = function(source, 'sec_bat_store_attrs')
        case = setter.split('\tcase CHARGE_MAX_MODE:', 1)[1].split('\tcase BATT_LP_CHARGING:', 1)[0]
        self.assertIn('kstrtoint(buf, 10, &x)', case)
        self.assertIn('if (x != 0 && x != 1)', case)
        self.assertIn('&battery->siop_level_work, 0', case)
        self.assertIn('&battery->monitor_work, 0', case)

    def test_patch_reverse_restores_all_original_bytes(self):
        # Reverse checks also cover the repeat-build "already applied" branch.
        with tempfile.TemporaryDirectory(prefix='r8s-charging-reverse-') as temporary:
            dest = Path(temporary)
            shutil.copytree(self.directory / 'drivers', dest / 'drivers')
            for patch in reversed(PATCHES):
                subprocess.run(['git', '-c', 'core.autocrlf=false', 'apply', '--reverse', '--check', str(PATCH_DIR / patch)],
                               cwd=dest, check=True, capture_output=True)
                subprocess.run(['git', '-c', 'core.autocrlf=false', 'apply', '--reverse', str(PATCH_DIR / patch)],
                               cwd=dest, check=True, capture_output=True)
            for path, data in self.original.items():
                self.assertEqual((dest / path).read_bytes(), data)

    @unittest.skipUnless(os.environ.get('R8S_CHARGING_CC'), 'Optional native C compiler not configured')
    def test_actual_c_helpers_and_fixed_pdo_selection(self):
        helpers = '\n'.join(function(self.source, n) for n in [
            'sec_bat_charge_max_mode_active', 'sec_bat_effective_charge_siop', 'sec_bat_change_vbus_pd'])
        harness = r'''
#include <stdbool.h>
#include <assert.h>
#include <stdio.h>
#define READ_ONCE(v) (v)
#define CONFIG_SUPPORT_HV_CTRL 1
#define SEC_BATTERY_TEMP_CHECK_NONE 0
#define SEC_BATTERY_CABLE_PDIC 3
#define SEC_BAT_CURRENT_EVENT_SELECT_PDO 1
#define MAX_PDO_NUM 8
#define LRP_NONE 0
#define POWER_SUPPLY_PROP_CURRENT_MAX 0
#define set 0
#define pr_info(...) ((void)0)
union power_supply_propval { int intval; };
struct info { int max_current, input_current, pdo_index; };
struct platform { int chg_temp_check_type; const char *charger_name; };
struct sec_battery_info {
 bool charge_max_mode, charging_enabled, store_mode, pdic_ps_rdy;
 int cable_type, siop_level, current_event, input_current;
 struct platform *pdata;
 struct { int num_fpdo, max_pd_count, now_pd_index; struct info pd_info[8]; } pd_list;
};
static int selected, lrp, programmed;
static bool is_wired_type(int type) { return type == 3 || type == 4; }
static int sec_bat_get_lrp_step(struct sec_battery_info *b) { (void)b; return lrp; }
static void sec_bat_set_current_event(struct sec_battery_info *b, int event, int mask)
 { b->current_event = (b->current_event & ~mask) | event; }
static void select_pdo(int pdo) { selected = pdo; }
#define psy_do_property(name, operation, prop, value) ((void)(name), (void)(operation), (void)(prop), programmed=(value).intval)
'''
        main = r'''
int main(void) {
 struct platform pdata = {1, "charger"};
 struct sec_battery_info b = {0};
 int count = 0;
 for (int enabled=0; enabled<=1; enabled++)
 for (int charging=0; charging<=1; charging++)
 for (int store=0; store<=1; store++)
 for (int cable=0; cable<=4; cable++)
 for (int raw=0; raw<=100; raw++) {
  b.charge_max_mode=enabled; b.charging_enabled=charging;
  b.store_mode=store; b.cable_type=cable; b.siop_level=raw;
  int expected=(enabled && charging && !store && is_wired_type(cable) && raw>0) ? 100 : raw;
  assert(sec_bat_effective_charge_siop(&b)==expected);
  assert(b.siop_level==raw); count++;
 }
 b=(struct sec_battery_info){0};
 b.pdata=&pdata; b.cable_type=3; b.charging_enabled=true; b.siop_level=100;
 b.pd_list.num_fpdo=2; b.pd_list.max_pd_count=4;
 for(int i=0;i<8;i++) b.pd_list.pd_info[i]=(struct info){1000+i*100,1000+i*100,i+1};
 int input=3000;
 b.input_current=3000; b.pdic_ps_rdy=true;
 assert(sec_bat_change_vbus_pd(&b,&input));
#ifdef CONFIG_PDIC_PD30
 assert(selected==2 && input==1100 && programmed==1100);
#else
 assert(selected==4 && input==1300 && programmed==1300);
#endif
 assert(!b.pdic_ps_rdy);
 assert(!sec_bat_change_vbus_pd(&b,&input)); /* Pending negotiation. */
 b.current_event=0; b.pd_list.now_pd_index=1; b.siop_level=50;
 assert(sec_bat_change_vbus_pd(&b,&input) && selected==1);
 b.current_event=0; b.siop_level=100; lrp=1;
 assert(sec_bat_change_vbus_pd(&b,&input) && selected==1);
 lrp=0; b.current_event=0; b.store_mode=true;
 assert(!sec_bat_change_vbus_pd(&b,&input));
 b.store_mode=false; b.charging_enabled=false;
 assert(!sec_bat_change_vbus_pd(&b,&input));
#ifdef CONFIG_PDIC_PD30
 b.charging_enabled=true; b.pd_list.num_fpdo=0;
 assert(!sec_bat_change_vbus_pd(&b,&input));
 b.pd_list.num_fpdo=MAX_PDO_NUM+1;
 assert(!sec_bat_change_vbus_pd(&b,&input));
#endif
 printf("%d native C policy cases plus PDO negotiation guards passed\n",count);
 return 0;
}
'''
        source = self.directory / 'charging-contract.c'
        source.write_text(harness + helpers + main)
        compiler = os.environ['R8S_CHARGING_CC']
        prefix = [compiler, 'cc'] if Path(compiler).stem == 'zig' else [compiler]
        for pd30 in (False, True):
            exe = self.directory / ('charging-pd30.exe' if pd30 else 'charging-legacy.exe')
            flags = ['-DCONFIG_PDIC_PD30=1'] if pd30 else []
            result = subprocess.run(prefix + ['-std=c99', '-Wall', '-Wextra', '-Werror'] + flags +
                                    [str(source), '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('4040 native C policy cases', result.stdout)


if __name__ == '__main__':
    unittest.main()
