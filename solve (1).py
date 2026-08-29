#This code is slow, but don't try to make it faster.
#Found a bug? Don't fix it yourself. Contact: t.me/PichchaMala

import os
import io
import json
import stat
import zipfile
import shutil
import platform
import time
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


def unpack_crx(crx_path: str, extract_to: str) -> str:
    if not os.path.isfile(crx_path):
        raise FileNotFoundError(f"CRX file not found: {crx_path}")
    if os.path.exists(extract_to):
        shutil.rmtree(extract_to)
    os.makedirs(extract_to, exist_ok=True)
    with open(crx_path, "rb") as f:
        data = f.read()
    if data[:4] != b"Cr24":
        raise ValueError("Not a valid CRX file (bad magic number)")
    version = int.from_bytes(data[4:8], "little")
    if version == 2:
        pub_key_len = int.from_bytes(data[8:12], "little")
        sig_len = int.from_bytes(data[12:16], "little")
        header_len = 16 + pub_key_len + sig_len
    elif version == 3:
        header_len_field = int.from_bytes(data[8:12], "little")
        header_len = 12 + header_len_field
    else:
        raise ValueError(f"Unsupported CRX version: {version}")
    zip_data = data[header_len:]
    if zip_data[:2] != b"PK":
        raise ValueError(
            f"Header parsing failed — data doesn't start with ZIP signature. "
            f"header_len={header_len}, first bytes={zip_data[:8]}"
        )
    zip_path = os.path.join(extract_to, "_temp.zip")
    with open(zip_path, "wb") as f:
        f.write(zip_data)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_to)

    os.remove(zip_path)
    print(f"[unpack_crx] Extracted to: {extract_to}")
    return extract_to


def find_manifest_root(extract_dir: str) -> str:
    if os.path.isfile(os.path.join(extract_dir, "manifest.json")):
        return extract_dir
    for root, dirs, files in os.walk(extract_dir):
        if "manifest.json" in files:
            print(f"[find_manifest_root] manifest.json found in: {root}")
            return root
    raise FileNotFoundError("manifest.json not found in extracted CRX contents")

def validate_manifest(ext_dir: str) -> dict:
    manifest_path = os.path.join(ext_dir, "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    print(f"[validate_manifest] name: {manifest.get('name')}")
    print(f"[validate_manifest] version: {manifest.get('version')}")
    print(f"[validate_manifest] manifest_version: {manifest.get('manifest_version')}")
    return manifest


def get_platform_key() -> str:
    system = platform.system()
    machine = platform.machine().lower()

    if system == "Windows":
        return "win64" if "64" in platform.architecture()[0] else "win32"
    elif system == "Darwin":
        return "mac-arm64" if machine in ("arm64", "aarch64") else "mac-x64"
    elif system == "Linux":
        return "linux64"
    raise RuntimeError(f"Unsupported platform: {system} / {machine}")


def _extract_zip(content: bytes, dest_dir: str):
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        zf.extractall(dest_dir)


def _find_executable(root_dir: str, names) -> str:
    for r, dirs, files in os.walk(root_dir):
        for f in files:
            if f in names:
                full = os.path.join(r, f)
                if platform.system() != "Windows":
                    st = os.stat(full)
                    os.chmod(full, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
                return full
    raise FileNotFoundError(f"None of {names} found under {root_dir}")


def download_chrome(dest_dir: str = "cft_bundle", force: bool = False):
    chrome_dir = os.path.join(dest_dir, "chrome")
    driver_dir = os.path.join(dest_dir, "chromedriver")
    chrome_names = {"chrome", "chrome.exe", "Google Chrome for Testing"}
    driver_names = {"chromedriver", "chromedriver.exe"}

    if not force:
        try:
            chrome_bin = _find_executable(chrome_dir, chrome_names)
            driver_bin = _find_executable(driver_dir, driver_names)
            print("[download_chrome] Using cached bundle.")
            return chrome_bin, driver_bin
        except FileNotFoundError:
            pass

    plat = get_platform_key()
    print(f"[download_chrome] Platform detected: {plat}")

    resp = requests.get("https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json", timeout=30)
    resp.raise_for_status()
    data = resp.json()
    latest = data["versions"][-1]
    print(f"[download_chrome] Using version: {latest['version']}")

    chrome_url = next(
        d["url"] for d in latest["downloads"]["chrome"] if d["platform"] == plat
    )
    driver_url = next(
        d["url"] for d in latest["downloads"]["chromedriver"] if d["platform"] == plat
    )

    print(f"[download_chrome] Downloading Chrome browser...")
    shutil.rmtree(chrome_dir, ignore_errors=True)
    os.makedirs(chrome_dir, exist_ok=True)
    r = requests.get(chrome_url, timeout=300)
    r.raise_for_status()
    _extract_zip(r.content, chrome_dir)

    print(f"[download_chrome] Downloading matching chromedriver...")
    shutil.rmtree(driver_dir, ignore_errors=True)
    os.makedirs(driver_dir, exist_ok=True)
    r = requests.get(driver_url, timeout=300)
    r.raise_for_status()
    _extract_zip(r.content, driver_dir)

    chrome_bin = _find_executable(chrome_dir, chrome_names)
    driver_bin = _find_executable(driver_dir, driver_names)

    print(f"[download_chrome] Chrome binary: {chrome_bin}")
    print(f"[download_chrome] Chromedriver binary: {driver_bin}")
    return chrome_bin, driver_bin

def build_driver(crx_path: str, extract_dir: str = "unpacked_extension",
                  headless: bool = False, user_data_dir: str = None,
                  cft_dir: str = "cft_bundle"):
    raw_extract = unpack_crx(crx_path, extract_dir)
    ext_dir = find_manifest_root(raw_extract)
    validate_manifest(ext_dir)
    abs_ext_path = os.path.abspath(ext_dir)
    chrome_bin, driver_bin = download_chrome(cft_dir)
    options = Options()
    options.binary_location = chrome_bin

    options.add_argument(f"--load-extension={abs_ext_path}")
    options.add_argument(f"--disable-extensions-except={abs_ext_path}")

    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--start-maximized")
    options.add_argument("--enable-logging")
    options.add_argument("--v=1")

    if user_data_dir:
        options.add_argument(f"--user-data-dir={os.path.abspath(user_data_dir)}")

    if headless:
        options.add_argument("--headless=new")

    service = Service(executable_path=driver_bin)
    driver = webdriver.Chrome(service=service, options=options)
    return driver


def check_extension_loaded(driver):
    #driver.get("chrome://extensions/")
    #time.sleep(2)
    # driver.save_screenshot("extensions_page.png")
    # print("[check_extension_loaded] Screenshot saved: extensions_page.png")
    #print("[check_extension_loaded] Chrome version:", driver.capabilities.get("browserVersion"))
    try:
        for entry in driver.get_log("browser"):
            print("[browser log]", entry["message"])
    except Exception as e:
        print(f"[check_extension_loaded] Could not fetch browser logs: {e}")



def test_recaptcha_v2(url):
    try:
        print(f"\nOpening:\n{url}")
        driver.get(url)
        captcha = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, ".g-recaptcha")
            )
        )

        print("\nreCAPTCHA container detected.")
        sitekey = captcha.get_attribute("data-sitekey")
        if not sitekey:
            match = re.search(
                r"(6L[\w-]{30,})",
                driver.page_source
            )
            if match:
                sitekey = match.group(1)

        if sitekey:
            print(f"Site key: {sitekey}")
        else:
            print("Site key not found.")

        print(
            "\nPlease wait while CAPTCHA is being verified..."
        )
        WebDriverWait(driver, 120).until(
            lambda d: d.execute_script("""
                const elements = document.querySelectorAll(
                    '[name="g-recaptcha-response"]'
                );

                for (const el of elements) {
                    if (el.value && el.value.length > 0) {
                        return true;
                    }
                }

                return false;
            """)
        )
        token = driver.execute_script("""
            const elements = document.querySelectorAll(
                '[name="g-recaptcha-response"]'
            );

            for (const el of elements) {
                if (el.value && el.value.length > 0) {
                    return el.value;
                }
            }

            return '';
        """)

        print("reCAPTCHA completed successfully.")
        print("\nToken:")
        print(token)
        driver.quit()

    except Exception as e:
        print(f"\nError: {e}")
        driver.quit()

    finally:
        driver.quit()


if __name__ == "__main__":
    CRX_PATH = "logic.crx"  

    driver = build_driver(
        crx_path=CRX_PATH,
        extract_dir="unpacked_extension",
        headless=True,
        user_data_dir=None,
        cft_dir="cft_bundle",  
    )

    try:
        check_extension_loaded(driver)
        
        url = ("https://www.google.com/recaptcha/api2/demo")

        test_recaptcha_v2(url)
        time.sleep(3)

    finally:
        driver.quit()
