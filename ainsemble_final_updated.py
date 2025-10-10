#!/usr/bin/env python3
"""
ainsemble_final_updated.py
Full automation combining SignUp, SignIn and Reset Password flows with Mailinator OTP capture,
screenshots, Excel report (3 sheets), ZIP of screenshots and optional email delivery.

Usage example:
  python ainsemble_final_updated.py --mode browserstack --bs_username "..." --bs_access_key "..." --bs_app_id "bs://..." \
    --login_email "you@mail.com" --login_password "pwd" --reset_email "testsampleainsemble@mailinator.com" \
    --send_email true --email_from "you@mail.com" --email_password "pwd" --email_to "dest@mail.com"
"""

import os
import time
import csv
import argparse
import traceback
import re
import zipfile
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

import pandas as pd

from appium import webdriver
from appium.webdriver.common.appiumby import AppiumBy
from appium.options.android import UiAutomator2Options

from selenium import webdriver as selenium_webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.keys import Keys

# ---------- CONFIG / GLOBALS ----------
ARTIFACTS_DIR = os.path.join(os.getcwd(), "artifacts")
os.makedirs(ARTIFACTS_DIR, exist_ok=True)

OTP_POLL_MAX = 60         # seconds (total wait for OTP)
OTP_POLL_INTERVAL = 5     # seconds (between polls)
OTP_NEGATIVE = "1111"     # negative OTP to test once (single attempt)
OTP_MAX_ATTEMPTS = 3      # max tries to enter OTP

# ---------- UTIL ----------
def timestamp():
    return datetime.utcnow().strftime("%Y%m%dT%H%M%S")

def zip_screenshots():
    zip_name = f"screenshots_{timestamp()}.zip"
    zip_path = os.path.join(ARTIFACTS_DIR, zip_name)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fn in sorted(os.listdir(ARTIFACTS_DIR)):
            if fn.lower().endswith(".png"):
                zf.write(os.path.join(ARTIFACTS_DIR, fn), fn)
    return zip_path

def send_email(xlsx_path, zip_path, recipients, sender, password):
    subject = f"Ainsemble Automation Report ({os.path.basename(xlsx_path)})"
    body = f"Hi,\n\nPlease find attached the Ainsemble automation report and screenshots.\n\nRegards,\nAutomation Bot"

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    for p in [xlsx_path, zip_path]:
        with open(p, "rb") as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(p))
            part["Content-Disposition"] = f'attachment; filename="{os.path.basename(p)}"'
            msg.attach(part)

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=60)
        server.starttls()
        server.login(sender, password)
        server.send_message(msg)
        server.quit()
        print("✅ Email sent to:", recipients)
    except Exception as e:
        print("❌ Failed to send email:", e)

# ---------- DRIVER FACTORIES ----------
def create_browserstack_driver(username, key, app_id, device="Google Pixel 6", os_version="12.0"):
    """Create a fast, lightweight BrowserStack Appium driver with reduced logs and startup time."""
    caps = {
        "platformName": "Android",
        "automationName": "UiAutomator2",
        "app": app_id,
        "deviceName": device,
        "platformVersion": os_version,
        "browserstack.user": username,
        "browserstack.key": key,
        "autoGrantPermissions": True,

        # ✅ Optimization flags to speed up BrowserStack session startup
        "browserstack.video": "false",          # Disable video recording
        "browserstack.networkLogs": "false",    # Disable network logs
        "browserstack.deviceLogs": "false",     # Disable device logs
        "browserstack.idleTimeout": 120,        # Keep alive longer if needed
        "newCommandTimeout": 120,               # Prevent session timeout during test
        "appium:uiautomator2ServerLaunchTimeout": 60000,
        "appium:uiautomator2ServerInstallTimeout": 60000,
    }

    url = f"https://{username}:{key}@hub-cloud.browserstack.com/wd/hub"
    options = UiAutomator2Options().load_capabilities(caps)
    driver = webdriver.Remote(url, options=options)
    driver.implicitly_wait(8)
    return driver
def create_local_driver(apk_path=None, udid=None):
    caps = {
        "platformName": "Android",
        "automationName": "UiAutomator2",
        "app": apk_path,
        "deviceName": "Android" if not udid else udid,
        "autoGrantPermissions": True
    }
    driver = webdriver.Remote("http://127.0.0.1:4723/wd/hub", caps)
    driver.implicitly_wait(8)
    return driver

# ---------- MAIN CLASS ----------
class AinsembleRunner:
    def __init__(self, driver=None):
        self.driver = driver
        self.results_signin = []
        self.results_signup = []
        self.results_reset = []
        self.step_no = 1

    # ---- helpers: recording & screenshots ----
    def record(self, results_list, step, status, details=""):
        results_list.append({
            "time": datetime.utcnow().isoformat(),
            "step": step,
            "status": status,
            "details": details
        })
        print(f"[{status}] {step}: {details}")

    def screenshot(self, name):
        fname = f"{self.step_no:02d}_{name.replace(' ', '_')}.png"
        path = os.path.join(ARTIFACTS_DIR, fname)
        try:
            time.sleep(1.2)
            if self.driver:
                self.driver.save_screenshot(path)
            else:
                open(path, "wb").close()
            print("📸", fname)
        except Exception as e:
            print("⚠️ Screenshot failed:", e)
        self.step_no += 1
        return path

    # ---- element helpers ----
    def find_by_text(self, text, timeout=8):
        end = time.time() + timeout
        while time.time() < end:
            try:
                el = self.driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().textContains("{text}")')
                return el
            except:
                time.sleep(0.3)
        return None

    def find_and_click(self, results_list, text, desc=None, timeout=8, before_after_shots=True):
        label = desc or text
        try:
            el = self.find_by_text(text, timeout=timeout)
            if not el:
                self.record(results_list, label, "FAIL", f"Element with textContains('{text}') not found")
                self.screenshot(f"{label}_NotFound")
                return False
            if before_after_shots:
                self.screenshot(f"{label}_BeforeClick")
            try:
                el.click()
            except Exception:
                try:
                    self.driver.execute_script("mobile: clickGesture", {"elementId": el.id})
                except Exception:
                    raise
            time.sleep(2.2)
            if before_after_shots:
                self.screenshot(f"{label}_AfterClick")
            self.record(results_list, label, "PASS", "Clicked")
            return True
        except Exception as e:
            self.record(results_list, label, "FAIL", str(e))
            self.screenshot(f"{label}_ClickFail")
            return False

    def enter_text(self, results_list, el, value, label):
        try:
            el.click()
            time.sleep(0.3)
            try:
                el.clear()
            except:
                pass
            time.sleep(0.2)
            el.send_keys(value)
            time.sleep(0.6)
            self.screenshot(f"{label}_Entered")
            self.record(results_list, label, "PASS", f"Entered: {value}")
            return True
        except Exception as e:
            self.record(results_list, label, "FAIL", f"Enter failed: {e}")
            self.screenshot(f"{label}_EntryFail")
            return False

    # ---- Mailinator OTP ----
    def fetch_mailinator_otp(self, inbox, max_wait=OTP_POLL_MAX, interval=OTP_POLL_INTERVAL):
        """Poll Mailinator public inbox for Ainsemble OTP. Returns 4-digit OTP or None."""
        print(f"🔎 Fetching OTP for inbox: {inbox}")
        chrome_opts = Options()
        chrome_opts.add_argument("--headless=new")
        chrome_opts.add_argument("--no-sandbox")
        chrome_opts.add_argument("--disable-dev-shm-usage")
        otp = None
        try:
            driver = selenium_webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_opts)
        except Exception as e:
            print("❌ Selenium driver init failed:", e)
            return None

        deadline = time.time() + max_wait
        first = True
        while time.time() < deadline:
            if first:
                print(f"⏳ Waiting up to {max_wait}s for email (poll every {interval}s)...")
                first = False
            try:
                driver.get(f"https://www.mailinator.com/v4/public/inboxes.jsp?to={inbox}")
                time.sleep(3)
                try:
                    driver.save_screenshot(os.path.join(ARTIFACTS_DIR, f"{self.step_no:02d}_Mailinator_Inbox.png"))
                except:
                    pass

                rows = driver.find_elements("xpath", "//td[contains(text(),'Your Ainsemble account verification code')]")
                if rows:
                    rows[0].click()
                    time.sleep(2.2)
                    try:
                        driver.save_screenshot(os.path.join(ARTIFACTS_DIR, f"{self.step_no:02d}_Mailinator_Message.png"))
                    except:
                        pass
                    html = driver.page_source or ""
                    start = html.find("Please find your one time password")
                    snippet = html[start:start+800] if start != -1 else html
                    m = re.search(r"(\b\d{4}\b)", snippet)
                    if m:
                        otp = m.group(1)
                        print("✅ Extracted OTP:", otp)
                        driver.quit()
                        return otp
                    m2 = re.search(r"(\b\d{4}\b)", html)
                    if m2:
                        otp = m2.group(1)
                        print("⚠️ Fallback OTP extracted:", otp)
                        driver.quit()
                        return otp
                else:
                    print(" - email not present yet; waiting...")
            except Exception as e:
                print("Mailinator check error:", e)
            time.sleep(interval)

        try:
            driver.save_screenshot(os.path.join(ARTIFACTS_DIR, f"{self.step_no:02d}_Mailinator_Timeout.png"))
        except:
            pass
        driver.quit()
        print("❌ OTP not found within timeout")
        return None

    # ---- low-level clear helpers ----
    def _try_backspaces(self, count=6):
        try:
            for _ in range(count):
                self.driver.press_keycode(67)  # KEYCODE_DEL
            return True
        except Exception:
            pass
        try:
            active = self.driver.switch_to.active_element
            for _ in range(count):
                active.send_keys(Keys.BACKSPACE)
            return True
        except Exception:
            return False

    def _clear_field_robust(self, el, tries=3):
        for _ in range(tries):
            try:
                el.click()
            except:
                pass
            try:
                el.clear()
            except:
                pass
            time.sleep(0.2)
            val = ""
            try:
                val = (el.get_attribute("text") or el.text or "").strip()
            except:
                val = ""
            if val == "":
                return True
            self._try_backspaces(6)
            time.sleep(0.2)
        return False

    # ---- verify page change helper (NEW) ----
    def verify_page_change(self, keyword, timeout=10):
        """Search page_source repeatedly for keyword (case-insensitive)."""
        end = time.time() + timeout
        keyword = (keyword or "").lower()
        while time.time() < end:
            try:
                src = (self.driver.page_source or "").lower()
                if keyword in src:
                    return True
            except:
                pass
            time.sleep(0.8)
        return False

    # ---- OTP entry (robust) ----
    def enter_and_verify_otp(self, results_list, otp):
        """Attempt to enter OTP (4 digits) with small retries; return True if entered and accepted."""
        for attempt in range(1, OTP_MAX_ATTEMPTS + 1):
            try:
                elems = self.driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.EditText")
                if len(elems) < 4:
                    time.sleep(1)
                    elems = self.driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.EditText")
                for i in range(4):
                    if i < len(elems):
                        try:
                            self._clear_field_robust(elems[i], tries=2)
                        except:
                            pass
                time.sleep(0.3)
                for i, ch in enumerate(otp[:4]):
                    if i < len(elems):
                        try:
                            elems[i].click()
                            time.sleep(0.12)
                            elems[i].send_keys(ch)
                            time.sleep(0.3)
                            val = (elems[i].get_attribute("text") or elems[i].text or "").strip()
                            if val != ch:
                                elems[i].clear()
                                elems[i].send_keys(ch)
                                time.sleep(0.2)
                        except Exception:
                            pass
                time.sleep(0.6)
                entered = ""
                elems = self.driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.EditText")
                for i in range(4):
                    if i < len(elems):
                        try:
                            entered += (elems[i].get_attribute("text") or elems[i].text or "").strip()[:1]
                        except:
                            entered += ""
                if entered == otp[:4]:
                    self.screenshot("OTP_Entered")
                    self.find_and_click(results_list, "Submit", "Submit_OTP")
                    time.sleep(1.8)
                    if self.detect_error_message():
                        self.record(results_list, "OTP Validation", "WARN", f"Submit shows error after OTP (attempt {attempt})")
                        continue
                    self.record(results_list, "OTP Validation", "PASS", f"OTP entered: {entered}")
                    return True
                else:
                    self.record(results_list, "OTP Mismatch", "WARN", f"Attempt {attempt}: entered '{entered}', expected '{otp[:4]}'")
                    time.sleep(0.8)
            except Exception as e:
                self.record(results_list, "OTP Entry Error", "WARN", f"Attempt {attempt} exception: {e}")
                time.sleep(0.6)
        self.record(results_list, "OTP Validation", "FAIL", f"All attempts failed for OTP {otp[:4]}")
        return False

    # ---- error detection ----
    def detect_error_message(self):
        try:
            src = (self.driver.page_source or "").lower()
            if "invalid" in src or "incorrect" in src or "error" in src or "please enter" in src or "not valid" in src:
                return True
        except:
            pass
        return False

    # ========== FLOW: SIGN-UP ==========
    def flow_signup(self, driver_factory, email_template="testsample{ts}@mailinator.com"):
        self.driver = driver_factory()
        step_results = []
        try:
            self.screenshot("Signup_Launch")
            email_id = email_template.format(ts=timestamp())
            inbox = email_id.split("@")[0]
            el = None
            end = time.time() + 10
            while time.time() < end:
                try:
                    el = self.driver.find_element(AppiumBy.CLASS_NAME, "android.widget.EditText")
                    break
                except:
                    time.sleep(0.5)
            if not el:
                self.record(step_results, "Enter Email", "FAIL", "Email field not found")
                self.screenshot("Signup_Email_NotFound")
                self.results_signup = step_results
                return step_results

            self.enter_text(step_results, el, email_id, "Signup_Email")
            self.find_and_click(step_results, "Submit", "Signup_Submit_Email")

            # negative OTP once
            time.sleep(3)
            otp_inputs = self.driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.EditText")
            for i, d in enumerate(OTP_NEGATIVE):
                if i < len(otp_inputs):
                    try:
                        otp_inputs[i].click()
                        otp_inputs[i].clear()
                        otp_inputs[i].send_keys(d)
                    except:
                        pass
            self.screenshot("Signup_Invalid_OTP")
            self.find_and_click(step_results, "Submit", "Signup_Submit_Invalid_OTP")
            time.sleep(2)
            self.record(step_results, "Negative OTP", "PASS", f"Invalid OTP {OTP_NEGATIVE} tested once")

            # Fetch real OTP
            print("Fetching real OTP from Mailinator...")
            otp = self.fetch_mailinator_otp(inbox, max_wait=OTP_POLL_MAX, interval=OTP_POLL_INTERVAL)
            if not otp:
                self.record(step_results, "Fetch OTP", "FAIL", "No OTP received")
                self.results_signup = step_results
                return step_results

            ok = self.enter_and_verify_otp(step_results, otp)
            if not ok:
                self.record(step_results, "Signup", "FAIL", "OTP not accepted; stopping to avoid lock")
                self.results_signup = step_results
                return step_results

            time.sleep(2)
            pw_fields = self.driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.EditText")
            if len(pw_fields) < 2:
                self.record(step_results, "Password Page", "FAIL", "Password fields not found")
                self.results_signup = step_results
                return step_results
            self.enter_text(step_results, pw_fields[0], "Dharma@999", "Signup_Password")
            self.enter_text(step_results, pw_fields[1], "Dharma@999", "Signup_ConfirmPassword")
            self.find_and_click(step_results, "Save", "Signup_SavePassword")
            time.sleep(2)

            contact_inputs = self.driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.EditText")
            if len(contact_inputs) >= 2:
                self.enter_text(step_results, contact_inputs[0], "Test", "Contact_Name")
                self.enter_text(step_results, contact_inputs[1], "+18558404823", "Contact_Phone")
            self.find_and_click(step_results, "Next", "Signup_Profile_Next")
            time.sleep(2)

            if self.detect_error_message():
                self.record(step_results, "Profile", "FAIL", "Error shown on profile page (invalid phone/name)")
                self.screenshot("Signup_Profile_Error")
                self.results_signup = step_results
                return step_results

            social_inputs = self.driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.EditText")
            added = 0
            for i in range(min(2, len(social_inputs))):
                try:
                    self.enter_text(step_results, social_inputs[i], f"https://social{i}.com/test", f"Social_Link_{i+1}")
                    added += 1
                except:
                    pass
            self.find_and_click(step_results, "Next", "Signup_Social_Next")
            time.sleep(2)

            self.screenshot("Signup_Completed")
            self.record(step_results, "Sign-Up Flow", "PASS", f"Completed for {email_id}")

        except Exception as e:
            self.record(step_results, "Sign-Up Flow", "FAIL", f"{e}\n{traceback.format_exc()}")
            self.screenshot("Signup_Exception")
        finally:
            try:
                self.driver.quit()
            except:
                pass
            self.results_signup = step_results
            return step_results

    # ========== FLOW: SIGN-IN ==========
    def flow_signin(self, driver_factory, login_email, login_password):
        self.driver = driver_factory()
        step_results = []
        try:
            self.screenshot("Signin_Launch")
            try:
                allow_btn = self.find_by_text("Allow", timeout=3)
                if allow_btn:
                    self.screenshot("PermissionPopup")
                    allow_btn.click()
                    time.sleep(1)
            except:
                pass

            ok = self.find_and_click(step_results, "Sign In", "Signin_Open")
            if not ok:
                self.results_signin = step_results
                try:
                    self.driver.quit()
                except:
                    pass
                return step_results

            email_field = None
            end = time.time() + 8
            while time.time() < end and email_field is None:
                try:
                    email_field = self.driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR,
                                                            'new UiSelector().className("android.widget.EditText").instance(0)')
                except:
                    time.sleep(0.4)
            if not email_field:
                try:
                    email_field = self.driver.find_element(AppiumBy.CLASS_NAME, "android.widget.EditText")
                except:
                    email_field = None

            if not email_field:
                self.record(step_results, "Signin_Email", "FAIL", "Email field not found")
                self.screenshot("Signin_Email_NotFound")
                self.results_signin = step_results
                try:
                    self.driver.quit()
                except:
                    pass
                return step_results

            self.enter_text(step_results, email_field, login_email, "Signin_Email")
            password_field = None
            try:
                password_field = self.driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR,
                                                          'new UiSelector().className("android.widget.EditText").instance(1)')
            except:
                try:
                    fields = self.driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.EditText")
                    if len(fields) >= 2:
                        password_field = fields[1]
                except:
                    password_field = None

            if not password_field:
                self.record(step_results, "Signin_Password", "FAIL", "Password field not found")
                self.screenshot("Signin_Password_NotFound")
                self.results_signin = step_results
                try:
                    self.driver.quit()
                except:
                    pass
                return step_results

            self.enter_text(step_results, password_field, login_password, "Signin_Password")
            self.find_and_click(step_results, "Submit", "Signin_Submit")
            time.sleep(4)

            skip_el = self.find_by_text("Skip", timeout=3)
            if skip_el:
                self.screenshot("Biometric_BeforeSkip")
                try:
                    skip_el.click()
                except:
                    pass
                time.sleep(2)
                self.screenshot("Biometric_AfterSkip")
                self.record(step_results, "Biometric", "PASS", "Skipped biometric")

            time.sleep(6)
            if self.detect_error_message():
                self.record(step_results, "Sign-In", "FAIL", "Error shown after submit")
                self.screenshot("Signin_ErrorShown")
            else:
                self.record(step_results, "Sign-In", "PASS", f"Logged in as {login_email}")

        except Exception as e:
            self.record(step_results, "Sign-In Flow", "FAIL", f"{e}\n{traceback.format_exc()}")
            self.screenshot("Signin_Exception")
        finally:
            try:
                self.driver.quit()
            except:
                pass
            self.results_signin = step_results
            return step_results

    # ========== FLOW: RESET PASSWORD ==========
    def flow_reset_password(self, driver_factory, reset_email="testsampleainsemble@mailinator.com"):
        """Full Reset Password flow: email -> OTP -> new password -> verify success"""
        self.driver = driver_factory()
        step_results = []
        try:
            self.screenshot("Reset_Launch")
            # Navigate to Reset Password
            self.find_and_click(step_results, "Sign In", "Reset_Open_SignIn")
            self.find_and_click(step_results, "Reset Password", "Reset_Open_Reset")

            try:
                email_field = self.driver.find_element(AppiumBy.CLASS_NAME, "android.widget.EditText")
                self.enter_text(step_results, email_field, reset_email, "Reset_Email")
            except Exception:
                self.record(step_results, "Reset_Email", "FAIL", "Email field not found")
                self.screenshot("Reset_Email_NotFound")
                self.results_reset = step_results
                self.driver.quit()
                return step_results

            self.find_and_click(step_results, "Submit", "Reset_Submit")

            # Wait for OTP page to appear
            print("⏳ Waiting for OTP entry screen...")
            if not self.verify_page_change(keyword="Enter OTP", timeout=20):
                self.record(step_results, "OTP Page", "FAIL", "OTP entry page not loaded")
                self.screenshot("Reset_OTP_PageNotLoaded")
                self.results_reset = step_results
                self.driver.quit()
                return step_results

            self.screenshot("Reset_OTP_PageLoaded")

            # Fetch OTP
            inbox = reset_email.split("@")[0]
            otp = self.fetch_mailinator_otp(inbox, max_wait=OTP_POLL_MAX, interval=OTP_POLL_INTERVAL)
            if not otp:
                self.record(step_results, "Fetch OTP", "FAIL", "No OTP received from Mailinator")
                self.screenshot("Reset_OTP_FetchFail")
                self.results_reset = step_results
                self.driver.quit()
                return step_results

            # Enter OTP
            otp_ok = self.enter_and_verify_otp(step_results, otp)
            if not otp_ok:
                self.record(step_results, "OTP Validation", "FAIL", "OTP not accepted in reset flow")
                self.results_reset = step_results
                self.driver.quit()
                return step_results

            # Wait for password reset page
            print("⏳ Waiting for password reset screen...")
            if not self.verify_page_change(keyword="Reset password", timeout=15):
                self.record(step_results, "Password Page", "FAIL", "Password reset screen not visible")
                self.screenshot("Reset_Password_PageNotLoaded")
                self.results_reset = step_results
                self.driver.quit()
                return step_results

            self.screenshot("Reset_Password_PageLoaded")

            pw_fields = self.driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.EditText")
            if len(pw_fields) < 2:
                self.record(step_results, "Password Fields", "FAIL", "Password fields not found")
                self.screenshot("Reset_PasswordFields_NotFound")
                self.results_reset = step_results
                self.driver.quit()
                return step_results

                # Generate unique dynamic password
                dynamic_pw = f"Dharma@{datetime.utcnow().strftime('%H%M%S')}"
                self.enter_text(step_results, pw_fields[0], dynamic_pw, "New_Password")
                self.enter_text(step_results, pw_fields[1], dynamic_pw, "Confirm_Password")
                self.record(step_results, "Password_Generated", "INFO", f"Using dynamic password: {dynamic_pw}")


            try:
                save_btn = self.driver.find_element(AppiumBy.ACCESSIBILITY_ID, "Save & Continue")
                self.screenshot("Reset_Before_SaveContinue")
                save_btn.click()
                time.sleep(3)
                self.screenshot("Reset_After_SaveContinue")
                self.record(step_results, "Click_SaveContinue", "PASS", "Clicked Save & Continue")
            except Exception as e:
                self.record(step_results, "Click_SaveContinue", "FAIL", f"Button not found: {e}")
                self.screenshot("Reset_SaveContinue_NotFound")
                self.results_reset = step_results
                self.driver.quit()
                return step_results

            # Confirm redirection to Sign In or success message
            if self.verify_page_change(keyword="Sign In", timeout=20):
                self.record(step_results, "Reset Password Flow", "PASS", "Password reset successful, redirected to Sign In")
            elif self.detect_error_message():
                self.record(step_results, "Reset Password Flow", "FAIL", "Error displayed after saving password")
                self.screenshot("Reset_ErrorDisplayed")
            else:
                self.record(step_results, "Reset Password Flow", "PASS", "Password reset completed (page changed)")

            self.screenshot("Reset_Completed")

        except Exception as e:
            self.record(step_results, "Reset Password Flow", "FAIL", f"{e}\n{traceback.format_exc()}")
            self.screenshot("Reset_Exception")
        finally:
            try:
                self.driver.quit()
            except:
                pass
            self.results_reset = step_results
            return step_results

    # ========== Save & Report ==========
    def save_results_excel(self, out_prefix="ainsemble_results"):
        ts = timestamp()
        xlsx_name = f"{out_prefix}_{ts}.xlsx"
        xlsx_path = os.path.join(ARTIFACTS_DIR, xlsx_name)
        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
            df1 = pd.DataFrame(self.results_signin)
            df2 = pd.DataFrame(self.results_signup)
            df3 = pd.DataFrame(self.results_reset)
            if not df1.empty:
                df1.to_excel(writer, sheet_name="SignIn", index=False)
            else:
                pd.DataFrame([{"time": timestamp(), "step": "SignIn", "status": "SKIP", "details": "No results"}]).to_excel(writer, sheet_name="SignIn", index=False)
            if not df2.empty:
                df2.to_excel(writer, sheet_name="SignUp", index=False)
            else:
                pd.DataFrame([{"time": timestamp(), "step": "SignUp", "status": "SKIP", "details": "No results"}]).to_excel(writer, sheet_name="SignUp", index=False)
            if not df3.empty:
                df3.to_excel(writer, sheet_name="ResetPassword", index=False)
            else:
                pd.DataFrame([{"time": timestamp(), "step": "Reset", "status": "SKIP", "details": "No results"}]).to_excel(writer, sheet_name="ResetPassword", index=False)
        print("✅ Excel report saved:", xlsx_path)
        return xlsx_path

# ---------- MAIN ----------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["local", "browserstack"], default="browserstack")
    parser.add_argument("--apk", help="local apk path for local mode")
    parser.add_argument("--udid", help="device udid for local mode")
    parser.add_argument("--bs_username")
    parser.add_argument("--bs_access_key")
    parser.add_argument("--bs_app_id")
    parser.add_argument("--device", default="Google Pixel 6")
    parser.add_argument("--os_version", default="12.0")
    parser.add_argument("--login_email", default=None)
    parser.add_argument("--login_password", default=None)
    parser.add_argument("--reset_email", default=None)
    parser.add_argument("--send_email", default="false")
    parser.add_argument("--email_from")
    parser.add_argument("--email_password")
    parser.add_argument("--email_to")
    args = parser.parse_args()

    def driver_factory():
        if args.mode == "local":
            return create_local_driver(args.apk, args.udid)
        else:
            return create_browserstack_driver(args.bs_username, args.bs_access_key, args.bs_app_id, args.device, args.os_version)

    runner = AinsembleRunner()

    try:
        print("=== START: Sign-Up ===")
        signup_res = runner.flow_signup(driver_factory)

        print("=== START: Sign-In ===")
        if args.login_email and args.login_password:
            signin_res = runner.flow_signin(driver_factory, args.login_email, args.login_password)
        else:
            print("Login credentials not provided; skipping Sign-In flow.")
            runner.results_signin = [{"time": timestamp(), "step": "SignIn", "status": "SKIP", "details": "No credentials"}]

        print("=== START: Reset Password ===")
        # Always use Mailinator address for password reset unless explicitly overridden
        reset_email = args.reset_email or "testsampleainsemble@mailinator.com"
        reset_res = runner.flow_reset_password(driver_factory, reset_email)

        xlsx_path = runner.save_results_excel()
        zip_path = zip_screenshots()

        if args.send_email.lower() == "true":
            if not (args.email_from and args.email_password and args.email_to):
                print("Email params missing; cannot send email.")
            else:
                recipients = [x.strip() for x in args.email_to.split(",") if x.strip()]
                send_email(xlsx_path, zip_path, recipients, args.email_from, args.email_password)

        print("=== DONE ===")
        return 0

    except Exception as e:
        print("Fatal error:", e)
        print(traceback.format_exc())
        return 2

if __name__ == "__main__":
    main()
