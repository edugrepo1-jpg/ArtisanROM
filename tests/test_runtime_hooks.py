"""Host JVM contracts for the actual framework hook Smali (no Android device).

Optional tools: the official Apktool 3.0.3 release JAR and dex2jar 2.4 lib
directory, supplied through APKTOOL_TEST_JAR and DEX2JAR_TEST_LIB. Assemble
the patched classes, translate their DEX to JVM bytecode and execute them
against narrow Android API stubs. This checks hook behavior, not Android boot.
"""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
PATCHES = [
    "unica/mods/csc/smali/system/framework/framework.jar/0001-Introduce-CscFeatureHooks.patch",
    "unica/mods/settings/smali/system/framework/framework.jar/0002-Introduce-SamsungPropsHooks.patch",
    "unica/mods/settings/smali/system/framework/framework.jar/0003-Introduce-FloatingFeatureHooks.patch",
    "unica/mods/settings/smali/system/framework/framework.jar/0004-Introduce-HideDeveloperStatusUtils.patch",
]


def added_smali(text):
    for section in text.split("diff --git ")[1:]:
        if "\n--- /dev/null\n" not in section:
            continue
        path = re.search(r"^\+\+\+ b/(.*\.smali)$", section, re.M)[1]
        body = section.split("@@\n", 1)[1].split("\n-- \n", 1)[0]
        yield path, "\n".join(line[1:] for line in body.splitlines() if line.startswith("+")) + "\n"


STUBS = {
    "android/app/ActivityThread.java": '''package android.app;
public class ActivityThread { public static String pkg;
public static String currentPackageName() { return pkg; } }''',
    "android/text/TextUtils.java": '''package android.text;
public class TextUtils { public static boolean isEmpty(CharSequence s) {
return s == null || s.length() == 0; } }''',
    "android/os/SemSystemProperties.java": '''package android.os;
import java.util.*;
public class SemSystemProperties {
public static Map<String,String> values = new HashMap<>();
public static int reads;
public static String get(String k) { return get(k, ""); }
public static String get(String k, String def) { reads++; return values.getOrDefault(k, def); }
public static int getInt(String k,int def) {
try { return Integer.parseInt(get(k)); } catch(NumberFormatException e) { return def; } }
public static boolean getBoolean(String k,boolean def) {
String v=get(k); return v.isEmpty()?def:v.equals("1")||v.equals("true"); }
}''',
    "android/os/Build.java": '''package android.os;
public class Build { public static String MODEL, DEVICE, BRAND, MANUFACTURER, PRODUCT;
public static class VERSION { public static int SEM_FIRST_SDK_INT; } }''',
    "android/content/ContentResolver.java": '''package android.content;
public class ContentResolver { public int user; public ContentResolver(int u) { user=u; } }''',
    "android/content/Context.java": '''package android.content;
public class Context {
public String getPackageName() { return "com.test"; }
public android.content.pm.PackageManager getPackageManager() { return new android.content.pm.PackageManager(); }
public ContentResolver getContentResolver() { return new ContentResolver(0); }
}''',
    "android/content/pm/ApplicationInfo.java": '''package android.content.pm;
public class ApplicationInfo { public int flags; }''',
    "android/content/pm/PackageManager.java": '''package android.content.pm;
public class PackageManager { public ApplicationInfo getApplicationInfo(String p,int f) {
return new ApplicationInfo(); } }''',
    "android/util/Log.java": '''package android.util;
public class Log { public static int e(String tag,String msg,Throwable e) { return 0; } }''',
    "android/provider/Settings.java": '''package android.provider;
import android.content.ContentResolver;
import java.util.*;
public class Settings {
public static Map<Integer,Map<String,String>> values = new HashMap<>();
public static int reads;
public static String read(ContentResolver r,String key) {
reads++; return values.getOrDefault(r.user,Collections.emptyMap()).get(key); }
public static class System { public static int getInt(ContentResolver r,String key,int def) {
String v=read(r,key); return v==null?def:Integer.parseInt(v); } }
public static class Secure {
public static String getString(ContentResolver r,String key) { return read(r,key); }
public static boolean putStringForUser(ContentResolver r,String key,String value,int user) {
values.computeIfAbsent(user,k->new HashMap<>()).put(key,value); return true; } }
}''',
}

ASSEMBLER = '''import java.io.File;
import brut.androlib.smali.SmaliBuilder;
import com.android.tools.smali.dexlib2.Opcodes;
import com.android.tools.smali.dexlib2.writer.builder.DexBuilder;
import com.android.tools.smali.dexlib2.writer.io.FileDataStore;
public class AssembleHooks {
public static void main(String[] args) throws Exception {
// These hooks use ordinary DEX 039 instructions, also supported by Android 16.
DexBuilder dex = new DexBuilder(new Opcodes(29));
SmaliBuilder smali = new SmaliBuilder(29);
for(int i=1;i<args.length;i++) if(!smali.buildFile(new File(args[i]),dex))
throw new Exception("Assembly failed: "+args[i]);
dex.writeTo(new FileDataStore(new File(args[0])));
} }'''

FRAMES = '''import java.io.*;
import java.util.zip.*;
import org.objectweb.asm.*;
public class ComputeFrames {
public static void main(String[] args) throws Exception {
try(ZipFile input=new ZipFile(args[0]); ZipOutputStream output=new ZipOutputStream(new FileOutputStream(args[1]))) {
for(var entries=input.entries();entries.hasMoreElements();) {
ZipEntry e=entries.nextElement(); byte[] data=input.getInputStream(e).readAllBytes();
if(e.getName().endsWith(".class")) {
ClassWriter writer=new ClassWriter(ClassWriter.COMPUTE_FRAMES);
new ClassReader(data).accept(writer,ClassReader.SKIP_FRAMES); data=writer.toByteArray(); }
output.putNextEntry(new ZipEntry(e.getName())); output.write(data); output.closeEntry();
} } } }'''

CONTRACT = '''import io.mesalabs.unica.*;
import android.os.SemSystemProperties;
import android.app.ActivityThread;
import android.content.ContentResolver;
import android.provider.Settings;
import java.util.*;
public class HooksContract {
static int checks;
static void eq(Object actual,Object expected) {
checks++; if(!Objects.equals(actual,expected)) throw new AssertionError(actual+" != "+expected); }
public static void main(String[] args) throws Exception {
SemSystemProperties.values.put("ro.board.platform","exynos990");
SemSystemProperties.values.put("ro.boot.em.model","SM-G780F");
eq(CscFeatureHooks.onGetBoolean("CscFeature_Common_SupportPrivateMode"),true);
eq(CscFeatureHooks.onGetBoolean("CscFeature_Wifi_SupportBlockSkipForSetupWizard"),false);
eq(CscFeatureHooks.onGetBoolean("CscFeature_Wifi_SupportNetworkConnectionsRequired"),false);
eq(CscFeatureHooks.onGetString("CscFeature_Common_ConfigYuva"),"");
eq(CscFeatureHooks.onGetString("CscFeature_VoiceCall_ConfigRecording"),"RecordingAllowed");
eq(CscFeatureHooks.onGetInt("CscFeature_Setting_ConfigTypeHelp"),0);
eq(CscFeatureHooks.onGetBoolean("unknown"),null);
eq(CscFeatureHooks.onGetBoolean(null),null);
eq(CscFeatureHooks.onGetString("unknown"),null);
eq(CscFeatureHooks.onGetInt("unknown"),null);
ActivityThread.pkg="com.sec.android.app.camera";
eq(CscFeatureHooks.onGetCountryCode(),"USA");
ActivityThread.pkg="com.android.systemui";
eq(CscFeatureHooks.onGetCountryCode(),null);
ActivityThread.pkg=null;
eq(CscFeatureHooks.onGetCountryCode(),null);
eq(FloatingFeatureHooks.onGetBoolean("SEC_FLOATING_FEATURE_GRAPHICS_SUPPORT_3D_SURFACE_TRANSITION_FLAG"),true);
eq(FloatingFeatureHooks.onGetInt("SEC_FLOATING_FEATURE_LCD_CONFIG_AOD_FULLSCREEN"),0);
eq(FloatingFeatureHooks.onGetBoolean("unknown"),null);
eq(FloatingFeatureHooks.onGetInt("unknown"),null);
eq(FloatingFeatureHooks.onGetString("unknown"),null);
String[] anim={"HighEnd","Mass","LowEnd","LowestEnd","LowestEnd"};
// Change the value within one process to catch incorrect memoization.
for(int i=0;i<anim.length;i++) {
SemSystemProperties.values.put("persist.sys.unica.launcher_anim_type",""+i);
eq(FloatingFeatureHooks.onGetString("SEC_FLOATING_FEATURE_LAUNCHER_CONFIG_ANIMATION_TYPE"),anim[i]); }
java.lang.reflect.Field f=SamsungPropsHooks.class.getDeclaredField("sSpoofModelProp");
f.setAccessible(true); f.setBoolean(null,true);
eq(SamsungPropsHooks.onSystemPropertiesGet("ro.product.model"),"SM-G780F");
SemSystemProperties.values.put("ro.boot.em.model","SM-G780F-test");
eq(SamsungPropsHooks.onSystemPropertiesGet("ro.product.model"),"SM-G780F-test");
f.setBoolean(null,false);
eq(SamsungPropsHooks.onSystemPropertiesGet("ro.product.model"),null);
eq(SamsungPropsHooks.onSystemPropertiesGet("ro.board.platform"),null);
ContentResolver primary=new ContentResolver(0), work=new ContentResolver(10);
Settings.values.put(0,new HashMap<>(Map.of("unica_hide_dev","1","unica_hide_dev_list","com.hidden")));
Settings.values.put(10,new HashMap<>(Map.of("unica_hide_dev","1","unica_hide_dev_list","com.work")));
SemSystemProperties.values.put("sys.boot_completed","1");
eq(HideDeveloperStatusUtils.shouldHideDevStatus(primary,"com.hidden","adb_enabled"),true);
eq(HideDeveloperStatusUtils.shouldHideDevStatus(primary,"com.visible","adb_enabled"),false);
eq(HideDeveloperStatusUtils.shouldHideDevStatus(work,"com.work","adb_wifi_enabled"),true);
eq(HideDeveloperStatusUtils.shouldHideDevStatus(work,"com.hidden","adb_enabled"),false);
Settings.values.get(0).put("unica_hide_dev","0");
eq(HideDeveloperStatusUtils.shouldHideDevStatus(primary,"com.hidden","adb_enabled"),false);
Settings.values.get(0).put("unica_hide_dev","1");
SemSystemProperties.values.put("sys.boot_completed","0");
eq(HideDeveloperStatusUtils.shouldHideDevStatus(primary,"com.hidden","adb_enabled"),false);
SemSystemProperties.values.put("sys.boot_completed","1");
eq(HideDeveloperStatusUtils.shouldHideDevStatus(null,"com.hidden","adb_enabled"),false);
eq(HideDeveloperStatusUtils.shouldHideDevStatus(primary,null,"adb_enabled"),false);
eq(HideDeveloperStatusUtils.shouldHideDevStatus(primary,"com.hidden",null),false);
int sr=Settings.reads, pr=SemSystemProperties.reads;
for(int i=0;i<10000;i++) eq(HideDeveloperStatusUtils.shouldHideDevStatus(primary,"com.hidden","screen_brightness"),false);
if(!Boolean.getBoolean("baseline")) { eq(Settings.reads,sr); eq(SemSystemProperties.reads,pr); }
System.out.println(checks+" behavior checks; unrelated reads: settings="+(Settings.reads-sr)+", properties="+(SemSystemProperties.reads-pr));
} }'''


class RuntimeHooksTests(unittest.TestCase):
    def test_patch_format(self):
        for relative in PATCHES:
            result = subprocess.run(["git", "apply", "--numstat", relative], cwd=ROOT,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(os.environ.get("APKTOOL_TEST_JAR") and os.environ.get("DEX2JAR_TEST_LIB"),
                         "Optional host JVM toolchain not configured")
    def test_actual_hook_bytecode_contract(self):
        java = os.environ.get("JAVA_TEST_EXECUTABLE", "java")
        javac = os.environ.get("JAVAC_TEST_EXECUTABLE", "javac")
        apktool = os.environ["APKTOOL_TEST_JAR"]
        dex2jar = str(Path(os.environ["DEX2JAR_TEST_LIB"]) / "*")
        with tempfile.TemporaryDirectory(prefix="r8s-hooks-") as directory:
            tmp = Path(directory)
            def run(*args):
                result = subprocess.run(args, cwd=tmp, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout
            smali = []
            for relative in PATCHES:
                for path, body in added_smali((ROOT / relative).read_text()):
                    target = tmp / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(body)
                    smali.append(str(target))
            (tmp / "AssembleHooks.java").write_text(ASSEMBLER)
            run(java, "-cp", apktool, "AssembleHooks.java", "hooks.dex", *smali)
            run(java, "-cp", dex2jar, "com.googlecode.dex2jar.tools.Dex2jarCmd",
                "-f", "-o", "hooks.jar", "hooks.dex")
            sources = []
            for relative, body in STUBS.items():
                target = tmp / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body)
                sources.append(str(target))
            (tmp / "HooksContract.java").write_text(CONTRACT)
            run(javac, "-cp", "hooks.jar", "-d", "classes", *sources, "HooksContract.java")
            # dex2jar does not emit JVM StackMapTable entries. Generate them
            # using ASM with the real classes/stubs before full JVM verification.
            (tmp / "ComputeFrames.java").write_text(FRAMES)
            run(java, "-cp", os.pathsep.join([dex2jar, "classes", "hooks.jar"]),
                "ComputeFrames.java", "hooks.jar", "verified.jar")
            output = run(java, "-Xverify:all", "-cp", "classes" + os.pathsep + "verified.jar", "HooksContract")
            self.assertIn("unrelated reads: settings=0, properties=0", output)
            print(output.strip())


if __name__ == "__main__":
    unittest.main(verbosity=2)
