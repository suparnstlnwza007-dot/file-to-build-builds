#!/usr/bin/env python3
# GitHub to Build - APK build script (runs on GitHub Actions)
# Usage: build.py --detect   -> writes mode=<mode> to GITHUB_OUTPUT
#        build.py            -> builds the APK into out/
import glob, html, json, os, re, shutil, subprocess, sys, zipfile

ROOT = os.getcwd()
cfg = json.load(open(os.path.join(ROOT, "config.json")))
BUILD_ID = cfg["buildId"]
SRC = os.path.join(ROOT, "_src")
OUT = os.path.join(ROOT, "out")
os.makedirs(SRC, exist_ok=True)
os.makedirs(OUT, exist_ok=True)
SKIP = ("node_modules", ".git", "build", ".gradle", ".dart_tool", ".buildozer", "platforms")


def run(cmd, cwd=None):
    print("+", cmd, flush=True)
    subprocess.run(cmd, shell=True, check=True, cwd=cwd)


def fail(msg):
    print("ERROR:", msg, flush=True)
    sys.exit(1)


def find(name):
    best = None
    for dp, dn, fn in os.walk(SRC):
        dn[:] = [d for d in dn if d not in SKIP]
        if name in fn and (best is None or dp.count(os.sep) < best.count(os.sep)):
            best = dp
    return best


def read(p):
    try:
        return open(p, encoding="utf-8", errors="ignore").read()
    except Exception:
        return ""


def pkg_json(d):
    try:
        return json.load(open(os.path.join(d, "package.json")))
    except Exception:
        return {}


def deps_of(pj):
    return {**pj.get("dependencies", {}), **pj.get("devDependencies", {})}


def output(k, v):
    with open(os.environ.get("GITHUB_OUTPUT", "/dev/null"), "a") as f:
        f.write(f"{k}={v}\n")


if os.path.exists("input.zip") and not os.listdir(SRC):
    with zipfile.ZipFile("input.zip") as z:
        z.extractall(SRC)

REPO = cfg.get("repo") or ""
if REPO and not os.listdir(SRC):
    # Public GitHub repo: clone it here (private repos arrive as input.zip).
    branch = f'--branch "{cfg["ref"]}" ' if cfg.get("ref") else ""
    subprocess.run(
        f'git clone --depth 1 {branch}--recurse-submodules --shallow-submodules "https://github.com/{REPO}.git" "{SRC}"',
        shell=True, check=True,
    )
if REPO:
    top = [e for e in os.listdir(SRC) if e != "__MACOSX"]
    # GitHub zip snapshots wrap everything in one "owner-repo-sha/" folder.
    if len(top) == 1 and os.path.isdir(os.path.join(SRC, top[0])) and not os.path.exists(os.path.join(SRC, ".git")):
        SRC = os.path.join(SRC, top[0])
    sub = (cfg.get("subdir") or "").strip("/")
    if sub:
        if ".." in sub.split("/") or not os.path.isdir(os.path.join(SRC, sub)):
            print("ERROR: ไม่พบโฟลเดอร์", sub, "ใน repo", flush=True)
            sys.exit(1)
        SRC = os.path.join(SRC, sub)


def detect():
    m = cfg.get("mode", "auto")
    if m != "auto":
        return m
    p = find("pubspec.yaml")
    if p and "flutter" in read(os.path.join(p, "pubspec.yaml")):
        return "flutter"
    pk = find("package.json")
    if pk:
        d = deps_of(pkg_json(pk))
        if "expo" in d:
            return "expo"
        if "react-native" in d:
            return "react-native"
    c = find("config.xml")
    if c and "<widget" in read(os.path.join(c, "config.xml")) and "cordova" in read(os.path.join(c, "config.xml")).lower():
        return "cordova"
    if find("capacitor.config.json") or find("capacitor.config.ts") or find("capacitor.config.js"):
        return "capacitor"
    if find("settings.gradle") or find("settings.gradle.kts"):
        return "android"
    if find("buildozer.spec"):
        return "kivy"
    mp = find("main.py")
    if mp and "kivy" in read(os.path.join(mp, "main.py")).lower():
        return "kivy"
    return "web"


mode = detect()
if "--detect" in sys.argv:
    print("Detected mode:", mode, flush=True)
    output("mode", mode)
    sys.exit(0)
print("Build mode:", mode, flush=True)

safe = re.sub(r"[^A-Za-z0-9_-]+", "_", cfg["appName"]).strip("_") or "app"
apk_out = os.path.join(OUT, f"{safe}-{cfg['versionName']}.apk")


def first_apk(base, prefer="release"):
    apks = glob.glob(base + "/**/outputs/**/*.apk", recursive=True) + glob.glob(base + "/**/bin/*.apk", recursive=True)
    apks = [a for a in apks if "androidTest" not in a and "unsigned" not in a]
    if not apks:
        fail("build สำเร็จแต่ไม่พบไฟล์ APK")
    apks.sort(key=lambda a: (prefer not in a, len(a)))
    return apks[0]


def gradle_build(proj, task="assembleDebug"):
    # Uploaded settings often omit Google's plugin repository. Configure it
    # before settings evaluation, preserving custom repositories and wrappers.
    init = os.path.join(ROOT, "ftb-repositories.gradle")
    with open(init, "w") as script:
        script.write("""beforeSettings { settings ->
    settings.pluginManagement.repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
settingsEvaluated { settings ->
    settings.dependencyResolutionManagement.repositories {
        google()
        mavenCentral()
    }
}
""")
    gw = os.path.join(proj, "gradlew")
    if os.path.exists(gw):
        os.chmod(gw, 0o755)
        g = "./gradlew"
    else:
        g = "gradle"
    run(f'{g} {task} --init-script "{init}" --no-daemon --stacktrace', cwd=proj)


def npm_install(d):
    if os.path.exists(os.path.join(d, "yarn.lock")):
        run("npx --yes yarn install --non-interactive", cwd=d)
    elif os.path.exists(os.path.join(d, "pnpm-lock.yaml")):
        run("npx --yes pnpm install", cwd=d)
    else:
        run("npm install --no-audit --no-fund --legacy-peer-deps", cwd=d)


# ---------------- native project types ----------------

def build_android():
    proj = find("settings.gradle") or find("settings.gradle.kts")
    if not proj:
        fail("ไม่พบ settings.gradle ในโปรเจกต์ Android")
    gradle_build(proj)
    shutil.copy(first_apk(proj, "debug"), apk_out)


def build_flutter():
    proj = find("pubspec.yaml")
    if not os.path.isdir(os.path.join(proj, "android")):
        org = ".".join(cfg["appId"].split(".")[:-1]) or "com.example"
        run(f"flutter create --platforms=android --org {org} .", cwd=proj)
    run("flutter pub get", cwd=proj)
    run(f"flutter build apk --release --build-name={cfg['versionName']} --build-number={int(cfg['versionCode'])}", cwd=proj)
    shutil.copy(os.path.join(proj, "build/app/outputs/flutter-apk/app-release.apk"), apk_out)


def build_react_native(expo):
    proj = find("package.json")
    npm_install(proj)
    if expo or not os.path.isdir(os.path.join(proj, "android")):
        run("npx --yes expo prebuild --platform android --no-install --clean", cwd=proj)
    gradle_build(os.path.join(proj, "android"), "assembleRelease")
    shutil.copy(first_apk(os.path.join(proj, "android"), "release"), apk_out)


def build_cordova():
    proj = find("config.xml")
    if os.path.exists(os.path.join(proj, "package.json")):
        npm_install(proj)
    run("npm install -g cordova", cwd=proj)
    if not os.path.isdir(os.path.join(proj, "platforms/android")):
        run("cordova platform add android", cwd=proj)
    run("cordova build android --debug", cwd=proj)
    shutil.copy(first_apk(os.path.join(proj, "platforms/android"), "debug"), apk_out)


def build_capacitor_project():
    proj = find("capacitor.config.json") or find("capacitor.config.ts") or find("capacitor.config.js")
    npm_install(proj)
    if pkg_json(proj).get("scripts", {}).get("build"):
        run("npm run build", cwd=proj)
    if "@capacitor/android" not in deps_of(pkg_json(proj)):
        run("npm install @capacitor/android@6 --legacy-peer-deps", cwd=proj)
    if not os.path.isdir(os.path.join(proj, "android")):
        run("npx cap add android", cwd=proj)
    run("npx cap sync android", cwd=proj)
    gradle_build(os.path.join(proj, "android"))
    shutil.copy(first_apk(os.path.join(proj, "android"), "debug"), apk_out)


def build_kivy():
    proj = find("buildozer.spec") or find("main.py")
    spec = os.path.join(proj, "buildozer.spec")
    if not os.path.exists(spec):
        run("buildozer init", cwd=proj)
        s = read(spec)
        parts = cfg["appId"].split(".")
        s = re.sub(r"^title = .*$", f"title = {cfg['appName']}", s, flags=re.M)
        s = re.sub(r"^package.name = .*$", f"package.name = {parts[-1]}", s, flags=re.M)
        s = re.sub(r"^package.domain = .*$", f"package.domain = {'.'.join(parts[:-1])}", s, flags=re.M)
        s = re.sub(r"^version = .*$", f"version = {cfg['versionName']}", s, flags=re.M)
        perms = ",".join(p.split(".")[-1] for p in cfg.get("permissions", []))
        if perms:
            s = re.sub(r"^#?\s*android.permissions = .*$", f"android.permissions = {perms}", s, flags=re.M)
        s = re.sub(r"^#?\s*android.accept_sdk_license = .*$", "android.accept_sdk_license = True", s, flags=re.M)
        open(spec, "w").write(s)
    run("yes | buildozer -v android debug", cwd=proj)
    shutil.copy(first_apk(proj, "debug"), apk_out)


# ---------------- web -> Capacitor wrapper ----------------

VIEW_EXT = {
    "img": (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"),
    "video": (".mp4", ".webm", ".mov"),
    "audio": (".mp3", ".wav", ".ogg", ".m4a"),
    "doc": (".pdf",),
}


def make_wrapper_site(www):
    """No index.html: generate one that runs/shows whatever was uploaded."""
    files = []
    for dp, dn, fn in os.walk(SRC):
        dn[:] = [d for d in dn if d not in SKIP]
        for f in fn:
            files.append(os.path.relpath(os.path.join(dp, f), SRC))
    if not files:
        fail("ไม่มีไฟล์ให้ build")
    shutil.copytree(SRC, www, dirs_exist_ok=True)
    head, body = [], []
    for f in sorted(files):
        lf = f.lower()
        e = html.escape(f)
        if lf.endswith(".css"):
            head.append(f'<link rel="stylesheet" href="{e}">')
        elif lf.endswith((".js", ".mjs")):
            body.append(f'<script src="{e}"{" type=module" if lf.endswith(".mjs") else ""}></script>')
        elif lf.endswith(VIEW_EXT["img"]):
            body.append(f'<img src="{e}" style="max-width:100%">')
        elif lf.endswith(VIEW_EXT["video"]):
            body.append(f'<video src="{e}" controls style="max-width:100%"></video>')
        elif lf.endswith(VIEW_EXT["audio"]):
            body.append(f'<p>{e}</p><audio src="{e}" controls></audio>')
        elif lf.endswith(VIEW_EXT["doc"]) or lf.endswith((".htm", ".html")):
            body.append(f'<p><a href="{e}">{e}</a></p>')
        elif lf.endswith((".txt", ".md", ".json", ".csv", ".xml", ".py", ".java", ".kt", ".c", ".cpp", ".ts", ".go", ".rs", ".php", ".rb", ".sh", ".yml", ".yaml")):
            body.append(f"<h3>{e}</h3><pre style='white-space:pre-wrap'>{html.escape(read(os.path.join(SRC, f)))}</pre>")
        else:
            body.append(f'<p><a href="{e}" download>{e}</a></p>')
    open(os.path.join(www, "index.html"), "w").write(
        "<!doctype html><html><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(cfg['appName'])}</title>{''.join(head)}</head>"
        f"<body style='font-family:sans-serif;margin:12px'>{''.join(body)}</body></html>"
    )


def find_web_dir():
    pk = find("package.json")
    if pk:
        pj = pkg_json(pk)
        if pj.get("scripts", {}).get("build"):
            npm_install(pk)
            env_cmd = "npm run build"
            if "next" in deps_of(pj):
                env_cmd = "npx next build && (npx next export || true)"
            run(env_cmd, cwd=pk)
            for d in ("dist", "build", "out", "www", "public", "dist/browser", ".output/public"):
                if os.path.exists(os.path.join(pk, d, "index.html")):
                    return os.path.join(pk, d)
            for dp, dn, fn in os.walk(os.path.join(pk, "dist")):
                if "index.html" in fn:
                    return dp
    return find("index.html")


def build_web(url_mode):
    APP = os.path.join(ROOT, "_cap")
    WWW = os.path.join(APP, "www")
    os.makedirs(APP, exist_ok=True)
    if url_mode:
        os.makedirs(WWW, exist_ok=True)
        open(os.path.join(WWW, "index.html"), "w").write("<!doctype html><title>loading</title>")
    else:
        web = find_web_dir()
        if web:
            shutil.copytree(web, WWW, ignore=shutil.ignore_patterns("node_modules", ".git"))
        else:
            print("ไม่พบ index.html -> สร้างหน้าแสดงไฟล์อัตโนมัติ", flush=True)
            make_wrapper_site(WWW)

    json.dump({"name": "capapp", "private": True, "version": "1.0.0"}, open(os.path.join(APP, "package.json"), "w"))
    plugins = [p for p in cfg.get("plugins", []) if re.match(r"^[a-z-]+$", p)]
    deps = "@capacitor/core@6 @capacitor/cli@6 @capacitor/android@6 " + " ".join(f"@capacitor/{p}@6" for p in plugins)
    run(f"npm install --no-audit --no-fund {deps}", cwd=APP)

    cap = {
        "appId": cfg["appId"],
        "appName": cfg["appName"],
        "webDir": "www",
        "android": {
            "allowMixedContent": bool(cfg.get("cleartext")),
            "webContentsDebuggingEnabled": bool(cfg.get("webDebug")),
        },
    }
    if url_mode:
        cap["server"] = {"url": cfg["url"], "cleartext": True}
    json.dump(cap, open(os.path.join(APP, "capacitor.config.json"), "w"), indent=2)
    run("npx cap add android", cwd=APP)
    run("npx cap sync android", cwd=APP)

    AND = os.path.join(APP, "android")
    man_p = os.path.join(AND, "app/src/main/AndroidManifest.xml")
    man = open(man_p).read()
    perms = set(cfg.get("permissions", [])) | {"android.permission.INTERNET"}
    lines = "".join(
        f'    <uses-permission android:name="{p}" />\n'
        for p in sorted(perms)
        if re.match(r"^[A-Za-z0-9_.]+$", p) and f'"{p}"' not in man
    )
    man = man.replace("<application", lines + "\n    <application", 1)
    man = re.sub(r'android:allowBackup="[^"]*"', f'android:allowBackup="{str(bool(cfg.get("allowBackup"))).lower()}"', man)
    if cfg.get("cleartext"):
        man = man.replace("<application", '<application android:usesCleartextTraffic="true"', 1)
    ori = cfg.get("orientation", "unspecified")
    if ori in ("portrait", "landscape", "sensor"):
        man = man.replace("<activity", f'<activity android:screenOrientation="{ori}"', 1)
    open(man_p, "w").write(man)

    vg = os.path.join(AND, "variables.gradle")
    v = open(vg).read()
    v = re.sub(r"minSdkVersion = \d+", f"minSdkVersion = {int(cfg['minSdk'])}", v)
    open(vg, "w").write(v)

    bg = os.path.join(AND, "app/build.gradle")
    b = open(bg).read()
    b = re.sub(r"versionCode \d+", f"versionCode {int(cfg['versionCode'])}", b)
    b = re.sub(r'versionName "[^"]*"', f'versionName "{cfg["versionName"]}"', b)
    open(bg, "w").write(b)

    color = cfg.get("statusBarColor", "#000000")
    if not re.match(r"^#[0-9a-fA-F]{6}$", color):
        color = "#000000"
    hide_status = cfg.get("hideStatusBar") or cfg.get("fullscreen")
    hide_nav = cfg.get("hideNavBar") or cfg.get("fullscreen")
    body = []
    if cfg.get("keepScreenOn"):
        body.append("getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);")
    if cfg.get("secure"):
        body.append("getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);")
    body.append(f'getWindow().setStatusBarColor(Color.parseColor("{color}"));')
    if cfg.get("fullscreen"):
        body.append("WindowCompat.setDecorFitsSystemWindows(getWindow(), false);")
    if hide_status:
        body.append("c.hide(WindowInsetsCompat.Type.statusBars());")
    if hide_nav:
        body.append("c.hide(WindowInsetsCompat.Type.navigationBars());")
    if hide_status or hide_nav:
        body.append("c.setSystemBarsBehavior(WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);")
    java_dir = os.path.join(AND, "app/src/main/java", *cfg["appId"].split("."))
    os.makedirs(java_dir, exist_ok=True)
    joined = "\n        ".join(body)
    open(os.path.join(java_dir, "MainActivity.java"), "w").write(
        f"""package {cfg['appId']};

import android.graphics.Color;
import android.os.Bundle;
import android.view.WindowManager;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.core.view.WindowInsetsControllerCompat;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {{
    @Override
    public void onCreate(Bundle savedInstanceState) {{
        super.onCreate(savedInstanceState);
        applyWindow();
    }}

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {{
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) applyWindow();
    }}

    private void applyWindow() {{
        WindowInsetsControllerCompat c = WindowCompat.getInsetsController(getWindow(), getWindow().getDecorView());
        {joined}
    }}
}}
"""
    )

    icon = os.path.join(ROOT, "icon.png")
    if os.path.exists(icon):
        res = os.path.join(AND, "app/src/main/res")
        shutil.rmtree(os.path.join(res, "mipmap-anydpi-v26"), ignore_errors=True)
        for d in glob.glob(res + "/mipmap-*"):
            for n in ("ic_launcher.png", "ic_launcher_round.png", "ic_launcher_foreground.png"):
                shutil.copy(icon, os.path.join(d, n))

    gradle_build(AND)
    shutil.copy(os.path.join(AND, "app/build/outputs/apk/debug/app-debug.apk"), apk_out)


BUILDERS = {
    "android": build_android,
    "flutter": build_flutter,
    "react-native": lambda: build_react_native(False),
    "expo": lambda: build_react_native(True),
    "cordova": build_cordova,
    "capacitor": build_capacitor_project,
    "kivy": build_kivy,
    "web": lambda: build_web(False),
    "url": lambda: build_web(True),
}
if mode not in BUILDERS:
    fail(f"ไม่รู้จักโหมด {mode}")
BUILDERS[mode]()

meta = {
    "appName": cfg["appName"],
    "appId": cfg["appId"],
    "versionName": cfg["versionName"],
    "versionCode": cfg["versionCode"],
    "mode": mode,
    "size": os.path.getsize(apk_out),
}
open(os.path.join(OUT, "release.md"), "w").write(
    f"{cfg['appName']} {cfg['versionName']} ({cfg['appId']}) · {mode}\n\n<!--meta {json.dumps(meta)} -->\n"
)
output("tag", f"apk-{BUILD_ID}")
output("title", f"{cfg['appName']} {cfg['versionName']}")
print("APK:", apk_out, flush=True)
