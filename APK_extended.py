#!/usr/bin/env python3
"""
APK_new.py - Ainsemble E2E automation (SignIn, SignUp w/ Mailinator OTP, Forgot Password)
Requirements:
  - appium-python-client
  - selenium
  - requests
Run on GitHub Actions with Chrome + chromedriver installed and chromedriver in PATH.
"""

import os
import time
import csv
import argparse
import subprocess
import traceback
import zipfile
import re
import random
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

# Appium / BrowserStack
from appium import webdriver
from appium.webdriver.common.appiumby import AppiumBy
from appium.options.android import UiAutomator2Options

# Selenium for Mailinator
from selenium import webdriver as webdrv
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# Globals
ARTIFACTS_DIR = os.path.join(os.getcwd(), 'artifacts')
os.makedirs(ARTIFACTS_DIR, exist_ok=True)


def now_ts():
    return datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')


def short_ts():
    return datetime.utcnow().strftime('%d%m%H%M%S')  # used for mailinator uniqueness


def adb_install(apk_path, udid=None):
    cmd = ['adb']
    if udid:
        cmd += ['-s', udid]
    cmd += ['install', '-r', apk_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result.returncode == 0, result.stdout + result.stderr


class APKNew:
    def __init__(self, driver, email_addr, password, cfg):
        self.driver = driver
        self.email = email_addr
        self.password = password
        self.cfg = cfg
        self.results = []

    # ---------------- utilities ----------------
    def _ts(self):
        return now_ts()

    def screenshot(self, name):
        path = os.path.join(ARTIFACTS_DIR, f"{self._ts()}_{name}.png")
        try:
            self.driver.save_screenshot(path)
            print("📸", path)
            return path
        except Exception as e:
            print("📸 Screenshot failed:", e)
            return None

    def record(self, step, status, details=""):
        print(f"[{status}] {step} - {details}")
        self.results.append({
            'time': datetime.utcnow().isoformat(),
            'step': step,
            'status': status,
            'details': details
        })

    def wait_for(self, by, selector, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            try:
                el = self.driver.find_element(by, selector)
                if el:
                    return el
            except:
                time.sleep(0.4)
        return None

    def find_elements(self, by, selector, timeout=3):
        end = time.time() + timeout
        while time.time() < end:
            try:
                els = self.driver.find_elements(by, selector)
                if els:
                    return els
            except:
                time.sleep(0.3)
        return []

    def dismiss_permission_if_any(self):
        try:
            btn = self.wait_for(AppiumBy.ID, "com.android.permissioncontroller:id/permission_allow_button", 3)
            if btn:
                self.screenshot('permission_popup')
                btn.click()
                time.sleep(1)
                self.screenshot('permission_dismissed')
                self.record('Permission dismissed', 'PASS', 'Clicked allow')
                return
            self.record('Permission popup', 'SKIP', 'No permission popup displayed')
        except Exception as e:
            self.record('Permission popup', 'FAIL', str(e))

    # ---------------- OTP helpers ----------------
    def find_otp_inputs(self):
        edits = self.find_elements(AppiumBy.CLASS_NAME, 'android.widget.EditText', timeout=2)
        candidates = []
        for el in edits:
            try:
                if not el.is_displayed():
                    continue
                txt = el.get_attribute('text') or ''
                if 'Enter Email' in txt or '@' in txt or 'Password' in txt or len(txt) > 20:
                    continue
                candidates.append(el)
            except:
                pass
        if len(candidates) >= 4:
            return candidates[:4]
        if len(edits) == 1:
            return edits
        return candidates if candidates else edits

    def enter_otp(self, otp):
        otp = str(otp)
        inputs = self.find_otp_inputs()
        if not inputs:
            self.record('Enter OTP', 'FAIL', 'No OTP inputs found')
            self.screenshot('otp_inputs_missing')
            return False
        if len(inputs) == 1:
            try:
                inputs[0].click()
                inputs[0].clear()
                inputs[0].send_keys(otp)
                self.screenshot('otp_single_filled')
                self.record('Enter OTP', 'PASS', f'Entered OTP {otp} into single field')
                return True
            except Exception as e:
                self.record('Enter OTP', 'FAIL', f'Exception: {e}')
                return False
        # distribute digits
        for i, f in enumerate(inputs):
            try:
                digit = otp[i] if i < len(otp) else ''
                f.click()
                f.clear()
                if digit:
                    f.send_keys(digit)
                time.sleep(0.15)
            except Exception as e:
                self.record('Enter OTP', 'FAIL', f'Failed at index {i}: {e}')
        self.screenshot('otp_multi_filled')
        self.record('Enter OTP', 'PASS', f'Entered digits for OTP {otp}')
        return True

    def is_logged_in(self, timeout=8):
        indicators = ['Home', 'Welcome', 'Profile', 'Logout', 'My Account', 'Get Started']
        for t in indicators:
            try:
                el = self.wait_for(AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().textContains("{t}")', timeout=3)
                if el:
                    return True
            except:
                pass
        otp_inputs = self.find_otp_inputs()
        submit_buttons = self.find_elements(AppiumBy.ACCESSIBILITY_ID, 'Submit', timeout=2)
        if not otp_inputs and not submit_buttons:
            return True
        return False

    # ---------------- Mailinator fetch ----------------
    def fetch_otp_mailinator_web(self, inbox_name, timeout=60):
        """
        Uses headless Selenium Chrome to fetch the public Mailinator inbox and extract the first 4-digit OTP.
        Requires chromedriver in PATH.
        """
        otp = None
        chrome_opts = ChromeOptions()
        chrome_opts.add_argument("--headless=new")
        chrome_opts.add_argument("--no-sandbox")
        chrome_opts.add_argument("--disable-dev-shm-usage")
        chrome_opts.add_argument("--disable-gpu")
        chrome_opts.add_argument("--window-size=1280,800")
        # start selenium driver
        try:
            service = ChromeService()  # uses chromedriver from PATH
            sdriver = webdrv.Chrome(service=service, options=chrome_opts)
        except Exception as e:
            self.record('Fetch OTP (Mailinator)', 'FAIL', f'ChromeDriver start failed: {e}')
            return None

        try:
            url = f"https://www.mailinator.com/v4/public/inboxes.jsp?to={inbox_name}"
            sdriver.get(url)
            wait = WebDriverWait(sdriver, 18)
            # Wait for message list or specific subject
            subj_keyword = "Your Ainsemble account verification code"
            try:
                wait.until(EC.presence_of_element_located((By.XPATH, f"//*[contains(text(),'{subj_keyword}') or contains(text(),'Ainsemble')]")))
            except TimeoutException:
                # continue; maybe page layout differs - do small pause
                time.sleep(2)

            found = False
            # Try to click first message matching Ainsemble subject
            try_xpaths = [
                f"//td[contains(text(),'{subj_keyword}')]",
                "//td[contains(.,'Ainsemble')]",
                "//div[contains(@class,'subject') and contains(., 'Ainsemble')]",
                "//div[contains(@class,'msglist-row')][1]",
                "//tr[contains(@class,'message-row')][1]"
            ]
            for xp in try_xpaths:
                try:
                    el = sdriver.find_element(By.XPATH, xp)
                    el.click()
                    found = True
                    break
                except Exception:
                    continue
            if not found:
                # click first message row generic
                try:
                    row = sdriver.find_element(By.XPATH, "//*[contains(@class,'msglist-row') or contains(@class,'all_message-min') or contains(@class,'message-row')]")
                    row.click()
                    found = True
                except Exception:
                    pass

            body_text = None
            end_time = time.time() + timeout
            while time.time() < end_time:
                try:
                    frames = sdriver.find_elements(By.TAG_NAME, "iframe")
                    if frames:
                        for f in frames:
                            try:
                                sdriver.switch_to.frame(f)
                                body = sdriver.find_element(By.TAG_NAME, "body")
                                body_text = body.text
                                sdriver.switch_to.default_content()
                                if body_text and len(body_text) > 10:
                                    break
                            except Exception:
                                sdriver.switch_to.default_content()
                                continue
                    if not body_text:
                        candidates = sdriver.find_elements(By.XPATH, "//*[contains(@class,'msg-body') or contains(@class,'mail_text') or contains(@class,'body') or contains(@id,'msg_body')]")
                        for p in candidates:
                            txt = p.text
                            if txt and len(txt) > 10:
                                body_text = txt
                                break
                    if body_text:
                        m = re.search(r'\b(\d{4})\b', body_text)
                        if m:
                            otp = m.group(1)
                            self.record('Fetch OTP (Mailinator)', 'PASS', f'Found OTP: {otp}')
                            break
                except Exception:
                    pass
                time.sleep(1)
            if not otp:
                self.record('Fetch OTP (Mailinator)', 'FAIL', 'OTP not found in message body within timeout')
            return otp
        finally:
            try:
                sdriver.quit()
            except:
                pass

    # ---------------- flows ----------------
    def run_signin(self):
        try:
            time.sleep(6)
            self.screenshot('app_launched')
            self.record('App launched', 'PASS', 'Splash done')

            self.dismiss_permission_if_any()

            sign_in = self.wait_for(AppiumBy.ACCESSIBILITY_ID, "Sign In", 10)
            if sign_in:
                sign_in.click()
                time.sleep(3)
                self.screenshot('sign_in_page')
                self.record('Sign-in page', 'PASS', 'Opened')
            else:
                self.record('Sign-in page', 'FAIL', 'Sign In button not found')
                return

            email_field = self.wait_for(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().className("android.widget.EditText").instance(0)', 8)
            if email_field:
                email_field.click()
                email_field.clear()
                email_field.send_keys(self.email)
                time.sleep(1)
                self.screenshot('signin_email_entered')
                self.record('Email entry (signin)', 'PASS', f'Entered {self.email}')
            else:
                self.record('Email entry (signin)', 'FAIL', 'Email field not found')
                return

            password_field = self.wait_for(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().className("android.widget.EditText").instance(1)', 8)
            if password_field:
                password_field.click()
                password_field.clear()
                password_field.send_keys(self.password)
                time.sleep(1)
                self.screenshot('signin_password_entered')
                self.record('Password entry (signin)', 'PASS', 'Entered password')
            else:
                self.record('Password entry (signin)', 'FAIL', 'Password field not found')
                return

            submit_btn = self.wait_for(AppiumBy.ACCESSIBILITY_ID, "Submit", 8)
            if submit_btn:
                submit_btn.click()
                time.sleep(6)
                self.screenshot('after_signin')
                self.record('After sign-in', 'PASS', 'Sign-in attempted')
            else:
                self.record('Submit (signin)', 'FAIL', 'Submit button not found')
        except Exception as e:
            self.screenshot('error_signin')
            self.record('Sign-in flow', 'FAIL', f'{e}\n{traceback.format_exc()}')

    def run_signup(self):
        try:
            self.driver.launch_app()
            time.sleep(3)
            self.screenshot('signup_start')

            # Click Sign up
            sign_up_btn = self.wait_for(AppiumBy.ACCESSIBILITY_ID, "Sign up", 6) or self.wait_for(AppiumBy.ACCESSIBILITY_ID, "Sign Up", 4)
            if not sign_up_btn:
                regs = self.find_elements(AppiumBy.ACCESSIBILITY_ID, "Register", timeout=3)
                sign_up_btn = regs[0] if regs else None
            if not sign_up_btn:
                self.record('Sign-up start', 'FAIL', 'Sign Up button not found')
                return
            sign_up_btn.click()
            time.sleep(2)
            self.screenshot('signup_page_opened')
            self.record('Sign-up page', 'PASS', 'Opened successfully')

            edits = self.find_elements(AppiumBy.CLASS_NAME, 'android.widget.EditText', timeout=5)
            if not edits:
                self.record('Sign-up email entry', 'FAIL', 'Email field not found')
                return
            email_field = edits[0]
            email_field.click()
            email_field.clear()
            email_field.send_keys(self.email)
            time.sleep(1)
            self.screenshot('signup_email_entered')
            self.record('Sign-up email', 'PASS', f'Entered {self.email}')

            submit_btn = self.wait_for(AppiumBy.ACCESSIBILITY_ID, "Submit", 6)
            if not submit_btn:
                self.record('Sign-up submit', 'FAIL', 'Submit button not found')
                return
            submit_btn.click()
            time.sleep(3)
            self.screenshot('signup_after_submit')
            self.record('Sign-up submitted', 'PASS', 'Clicked Submit to request OTP')

            # Wait for OTP presence (not always textual)
            otp_title = self.wait_for(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("Enter OTP")', 6)
            otp_fields = self.find_otp_inputs()
            if not otp_title and not otp_fields:
                self.record('OTP screen detection', 'SKIP', 'OTP screen not clearly detected, proceeding')

            # Negative OTP attempt
            neg = self.cfg.get('negative_otp', '1111')
            self.record('OTP negative test', 'INFO', f'Attempting negative OTP: {neg}')
            self.enter_otp(neg)
            sb = self.wait_for(AppiumBy.ACCESSIBILITY_ID, 'Submit', 4)
            if sb:
                sb.click()
            time.sleep(3)
            self.screenshot('otp_after_negative_submit')
            if self.is_logged_in(timeout=4):
                self.record('OTP negative test', 'FAIL', 'Negative OTP unexpectedly logged in')
            else:
                self.record('OTP negative test', 'PASS', 'Negative OTP rejected as expected')

            # Positive OTP retrieval from Mailinator
            otp_value = None
            if self.cfg.get('otp_mode') == 'mailinator_web':
                inbox = self.cfg.get('mailinator_inbox_local')
                self.record('Fetch OTP', 'INFO', f'Polling Mailinator inbox: {inbox}')
                otp_value = self.fetch_otp_mailinator_web(inbox, timeout=self.cfg.get('otp_timeout', 60))
            elif self.cfg.get('otp_mode') == 'manual':
                otp_value = self.cfg.get('manual_otp')
            # fallback
            if not otp_value:
                otp_value = self.cfg.get('default_positive_otp')
                if otp_value:
                    self.record('Fetch OTP', 'WARN', f'Falling back to default OTP {otp_value}')
            if not otp_value:
                self.record('OTP positive test', 'FAIL', 'No OTP available for positive test')
                return

            self.record('OTP positive test', 'INFO', f'Entering positive OTP: {otp_value}')
            self.enter_otp(otp_value)
            sb2 = self.wait_for(AppiumBy.ACCESSIBILITY_ID, 'Submit', 4)
            if sb2:
                sb2.click()
            time.sleep(6)
            self.screenshot('otp_after_positive_submit')

            if not self.is_logged_in(timeout=10):
                self.record('OTP positive test', 'FAIL', 'Did not reach next screen after OTP')
                return
            self.record('OTP positive test', 'PASS', 'Logged in after OTP')

            # ---------- Set Password ----------
            time.sleep(1)
            edits = self.find_elements(AppiumBy.CLASS_NAME, 'android.widget.EditText', timeout=6)
            if len(edits) >= 2:
                try:
                    pass_field = edits[0]
                    confirm_field = edits[1]
                    pass_field.click()
                    pass_field.clear()
                    pass_val = self.cfg.get('signup_password', 'Dharma@999')
                    pass_field.send_keys(pass_val)
                    confirm_field.click()
                    confirm_field.clear()
                    confirm_field.send_keys(pass_val)
                    self.screenshot('passwords_entered')
                    self.record('Set password', 'PASS', 'Entered password & confirm')
                except Exception as e:
                    self.record('Set password', 'FAIL', f'Exception: {e}')
            else:
                self.record('Set password', 'SKIP', 'Password fields not found')

            # Click Save & Continue (various label possibilities)
            save_btn = self.wait_for(AppiumBy.ACCESSIBILITY_ID, "Save & Continue", 6) or self.wait_for(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("Save & Continue")', 3)
            if save_btn:
                save_btn.click()
                time.sleep(3)
                self.screenshot('after_save_continue')
                self.record('Save & Continue', 'PASS', 'Clicked Save & Continue')
            else:
                self.record('Save & Continue', 'SKIP', 'Button not found')

            # ---------- Profile (Artist name & Phone) ----------
            time.sleep(1)
            edits = self.find_elements(AppiumBy.CLASS_NAME, 'android.widget.EditText', timeout=6)
            if edits and len(edits) >= 2:
                try:
                    edits[0].click()
                    edits[0].clear()
                    edits[0].send_keys(self.cfg.get('artist_name', 'Test'))
                    edits[1].click()
                    edits[1].clear()
                    edits[1].send_keys(self.cfg.get('artist_phone', '8558404823'))
                    self.screenshot('profile_filled')
                    self.record('Profile info', 'PASS', 'Entered Artist Name and Phone')
                except Exception as e:
                    self.record('Profile info', 'FAIL', f'Exception: {e}')
            else:
                self.record('Profile info', 'SKIP', 'Profile fields not found')

            next_btn = self.wait_for(AppiumBy.ACCESSIBILITY_ID, "Next", 6) or self.wait_for(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("Next")', 3)
            if next_btn:
                next_btn.click()
                time.sleep(2)
                self.screenshot('profile_next_clicked')
                self.record('Profile next', 'PASS', 'Clicked Next')
            else:
                self.record('Profile next', 'SKIP', 'Next button not found')

            # ---------- Social links - fill any two ----------
            time.sleep(1)
            social_edits = self.find_elements(AppiumBy.CLASS_NAME, 'android.widget.EditText', timeout=6)
            total = len(social_edits)
            if total >= 2:
                indices = list(range(total))
                random.shuffle(indices)
                chosen = indices[:2]
                for idx in chosen:
                    try:
                        elm = social_edits[idx]
                        elm.click()
                        elm.clear()
                        cur = elm.get_attribute('text') or ''
                        if not cur.strip():
                            value = "https://example.com/test"
                        else:
                            value = cur + ("test" if not cur.endswith("test") else "")
                        elm.send_keys(value)
                        time.sleep(0.3)
                    except Exception as e:
                        self.record('Social links', 'WARN', f'Could not fill field {idx}: {e}')
                self.screenshot('social_links_filled')
                self.record('Social links', 'PASS', f'Filled two social links indices {chosen}')
            else:
                self.record('Social links', 'SKIP', 'Less than 2 social link fields found')

            # Finish sign-up by clicking Next
            final_next = self.wait_for(AppiumBy.ACCESSIBILITY_ID, "Next", 6)
            if final_next:
                final_next.click()
                time.sleep(3)
                self.screenshot('signup_completed')
                self.record('Sign-up complete', 'PASS', 'Completed sign-up flow')
            else:
                self.record('Sign-up complete', 'SKIP', 'Final Next not found')

        except Exception as e:
            self.screenshot('signup_error')
            self.record('Sign-up flow', 'FAIL', f'{e}\n{traceback.format_exc()}')

    def run_forgot_password(self):
        try:
            self.driver.launch_app()
            time.sleep(3)
            self.screenshot('forgot_start')

            # navigate to Sign In if present
            si = self.find_elements(AppiumBy.ACCESSIBILITY_ID, 'Sign In', timeout=5)
            if si:
                try:
                    si[0].click()
                    time.sleep(1)
                except:
                    pass

            reset = self.find_elements(AppiumBy.ACCESSIBILITY_ID, 'Reset Password', timeout=4)
            if reset:
                try:
                    reset[0].click()
                except:
                    pass
            else:
                self.record('Forgot start', 'FAIL', 'Reset Password control not found')
                return
            time.sleep(1)
            self.screenshot('reset_page')
            self.record('Reset page', 'PASS', 'Opened Reset Password')

            edits = self.find_elements(AppiumBy.CLASS_NAME, 'android.widget.EditText', timeout=4)
            if not edits:
                self.record('Reset email entry', 'FAIL', 'Email field not found')
                return
            edits[0].click()
            edits[0].clear()
            edits[0].send_keys(self.email)
            time.sleep(1)
            self.screenshot('reset_email_entered')
            self.record('Reset email', 'PASS', f'Entered {self.email}')

            sub = self.wait_for(AppiumBy.ACCESSIBILITY_ID, 'Submit', 4)
            if sub:
                sub.click()
                time.sleep(2)
                self.screenshot('reset_after_submit')
                self.record('Reset submitted', 'PASS', 'Clicked Submit for reset')
            else:
                self.record('Reset submit', 'FAIL', 'Submit not found')
                return

            popup = self.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("Resend")', timeout=4)
            if popup:
                self.record('Reset popup', 'PASS', 'Reset confirmation popup found')
            else:
                self.record('Reset popup', 'SKIP', 'No explicit popup detected; check screenshot')

        except Exception as e:
            self.screenshot('forgot_error')
            self.record('Forgot flow', 'FAIL', f'{e}\n{traceback.format_exc()}')

    def save_results(self):
        csv_path = os.path.join(ARTIFACTS_DIR, f'results_{self._ts()}.csv')
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['time', 'step', 'status', 'details'])
            writer.writeheader()
            for r in self.results:
                writer.writerow(r)
        print('✅ Results saved to', csv_path)
        return csv_path


# ---------------- zip & email helpers ----------------
def zip_artifacts():
    zip_name = f"screenshots_{now_ts()}.zip"
    zip_path = os.path.join(ARTIFACTS_DIR, zip_name)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file in os.listdir(ARTIFACTS_DIR):
            if file.endswith('.png') or file.endswith('.csv'):
                zf.write(os.path.join(ARTIFACTS_DIR, file), file)
    print('🗂️ Artifacts zipped at', zip_path)
    return zip_path


def send_email(csv_path, zip_path, recipients, sender, password):
    total = 0
    passed = 0
    failed = 0
    skipped = 0
    # read CSV to count
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            total = len(rows)
            passed = len([r for r in rows if r['status'] == 'PASS'])
            failed = len([r for r in rows if r['status'] == 'FAIL'])
            skipped = len([r for r in rows if r['status'] == 'SKIP'])
    except Exception:
        pass

    subject = f"Ainsemble APK Automation Report ({passed}/{total} Passed)"
    body = f"""Ainsemble APK Automation completed.

Summary:
Total Steps: {total}
Passed: {passed}
Failed: {failed}
Skipped: {skipped}

Attachments:
1. {os.path.basename(csv_path)}
2. {os.path.basename(zip_path)}
"""
    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = ', '.join(recipients)
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    for path in [csv_path, zip_path]:
        try:
            with open(path, 'rb') as f:
                part = MIMEApplication(f.read(), Name=os.path.basename(path))
                part['Content-Disposition'] = f'attachment; filename="{os.path.basename(path)}"'
                msg.attach(part)
        except Exception as e:
            print("Attach failed:", e)

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender, password)
        server.send_message(msg)
        server.quit()
        print('✅ Email sent to:', recipients)
    except Exception as e:
        print('❌ Failed to send email:', e)


# ---------------- BrowserStack driver factory ----------------
def create_browserstack_driver(username, key, app_id, device, os_version):
    caps = {
        'platformName': 'Android',
        'automationName': 'UiAutomator2',
        'app': app_id,
        'deviceName': device,
        'platformVersion': os_version,
        'browserstack.user': username,
        'browserstack.key': key,
        'autoGrantPermissions': True
    }
    url = f'https://{username}:{key}@hub-cloud.browserstack.com/wd/hub'
    options = UiAutomator2Options().load_capabilities(caps)
    return webdriver.Remote(url, options=options)


# ---------------- CLI ----------------
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--mode', choices=['local', 'browserstack'], default='browserstack')
    p.add_argument('--apk')
    p.add_argument('--udid')
    p.add_argument('--bs_username')
    p.add_argument('--bs_access_key')
    p.add_argument('--bs_app_id')
    p.add_argument('--device', default='Google Pixel 6')
    p.add_argument('--os_version', default='12.0')
    p.add_argument('--login_email')  # optional — if empty, will generate testsample<ts>@mailinator.com
    p.add_argument('--login_password', default='Password123')
    p.add_argument('--send_email', default='false')
    p.add_argument('--email_to')
    p.add_argument('--email_from')
    p.add_argument('--email_password')

    p.add_argument('--otp_mode', choices=['manual', 'imap', 'mailinator_web', 'none'], default='mailinator_web')
    p.add_argument('--manual_otp', default='1234')
    p.add_argument('--negative_otp', default='1111')
    p.add_argument('--otp_timeout', type=int, default=60)
    p.add_argument('--mailinator_prefix', default='testsample')
    p.add_argument('--default_positive_otp', default='1234')

    p.add_argument('--signup_password', default='Dharma@999')
    p.add_argument('--artist_name', default='Test')
    p.add_argument('--artist_phone', default='8558404823')
    return p.parse_args()


def generate_mailinator(prefix):
    part = f"{prefix}{short_ts()}"
    return f"{part}@mailinator.com", part


def main():
    args = parse_args()

    # generate email if not provided
    if not args.login_email:
        login_email, inbox = generate_mailinator(args.mailinator_prefix)
    else:
        login_email = args.login_email
        m = re.match(r'([^@]+)@mailinator\.com$', login_email)
        inbox = m.group(1) if m else login_email.split('@')[0]

    cfg = {
        'otp_mode': args.otp_mode,
        'manual_otp': args.manual_otp,
        'negative_otp': args.negative_otp,
        'otp_timeout': args.otp_timeout,
        'mailinator_inbox_local': inbox,
        'default_positive_otp': args.default_positive_otp,
        'signup_password': args.signup_password,
        'artist_name': args.artist_name,
        'artist_phone': args.artist_phone
    }

    print("Using email:", login_email, "inbox:", inbox)

    driver = None
    try:
        # create BrowserStack driver
        driver = create_browserstack_driver(args.bs_username, args.bs_access_key, args.bs_app_id,
                                           args.device, args.os_version)

        validator = APKNew(driver, login_email, args.login_password, cfg)
        time.sleep(3)

        print("\n--- Sign-In ---")
        validator.run_signin()

        print("\n--- Sign-Up ---")
        validator.run_signup()

        print("\n--- Forgot Password ---")
        validator.run_forgot_password()

        csv_path = validator.save_results()
        zip_path = zip_artifacts()

        if args.send_email.lower() == 'true' and args.email_to:
            recipients = [x.strip() for x in args.email_to.split(',') if x.strip()]
            send_email(csv_path, zip_path, recipients, args.email_from, args.email_password)

    except Exception as e:
        print("Fatal:", e)
        print(traceback.format_exc())
    finally:
        try:
            if driver:
                driver.quit()
        except:
            pass


if __name__ == '__main__':
    main()
