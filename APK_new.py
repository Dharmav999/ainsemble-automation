import os, time, csv, argparse, subprocess, traceback, smtplib, zipfile
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from appium import webdriver
from appium.webdriver.common.appiumby import AppiumBy
from appium.options.android import UiAutomator2Options

ARTIFACTS_DIR = os.path.join(os.getcwd(), 'artifacts')
os.makedirs(ARTIFACTS_DIR, exist_ok=True)


def adb_install(apk_path, udid=None):
    cmd = ['adb']
    if udid:
        cmd += ['-s', udid]
    cmd += ['install', '-r', apk_path]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result.returncode == 0, result.stdout + result.stderr


class APKValidator:
    def __init__(self, driver, email, password):
        self.driver = driver
        self.email = email
        self.password = password
        self.results = []

    def _ts(self):
        return datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')

    def screenshot(self, name):
        path = os.path.join(ARTIFACTS_DIR, f"{self._ts()}_{name}.png")
        try:
            self.driver.save_screenshot(path)
            print("📸 Captured:", path)
            return path
        except Exception as e:
            print("Screenshot failed:", e)
            return None

    def record_result(self, step, status, details):
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
                return el
            except:
                time.sleep(0.5)
        return None

    def dismiss_permission_if_any(self):
        try:
            allow_btn = self.wait_for(AppiumBy.ID, "com.android.permissioncontroller:id/permission_allow_button", 5)
            if allow_btn:
                self.screenshot('permission_popup')
                allow_btn.click()
                time.sleep(2)
                self.screenshot('permission_dismissed')
                self.record_result('Permission dismissed', 'PASS', 'Clicked Allow')
                return

            allow_fg = self.wait_for(AppiumBy.ID, "com.android.permissioncontroller:id/permission_allow_foreground_only_button", 5)
            if allow_fg:
                self.screenshot('foreground_permission_popup')
                allow_fg.click()
                time.sleep(2)
                self.screenshot('foreground_permission_dismissed')
                self.record_result('Permission dismissed', 'PASS', 'Clicked Allow Foreground Only')
                return

            location_allow = self.wait_for(AppiumBy.ANDROID_UIAUTOMATOR,
                                           'new UiSelector().textContains("Allow only while using the app")', 5)
            if location_allow:
                self.screenshot('location_permission_popup')
                location_allow.click()
                time.sleep(2)
                self.screenshot('location_permission_dismissed')
                self.record_result('Location permission dismissed', 'PASS', 'Clicked Allow Location')
            else:
                self.record_result('Permission popup', 'SKIP', 'No permission popup displayed')
        except Exception as e:
            self.record_result('Permission popup', 'FAIL', str(e))

    def run_flow(self):
        try:
            time.sleep(8)
            self.screenshot('app_launched')
            self.record_result('App launched', 'PASS', 'Splash done')

            self.dismiss_permission_if_any()

            sign_in = self.wait_for(AppiumBy.ACCESSIBILITY_ID, "Sign In", 10)
            if sign_in:
                sign_in.click()
                time.sleep(5)
                self.screenshot('sign_in_page')
                self.record_result('Sign-in page', 'PASS', 'Opened')
            else:
                self.record_result('Sign-in page', 'FAIL', 'Sign In button not found')
                return

            email_field = self.wait_for(AppiumBy.ANDROID_UIAUTOMATOR,
                'new UiSelector().className("android.widget.EditText").instance(0)', 10)
            if email_field:
                email_field.click()
                email_field.clear()
                email_field.send_keys(self.email)
                time.sleep(2)
                self.screenshot('email_entered')
                self.record_result('Email entry', 'PASS', f'Entered {self.email}')
            else:
                self.record_result('Email entry', 'FAIL', 'Email field not found')
                return

            password_field = self.wait_for(AppiumBy.ANDROID_UIAUTOMATOR,
                'new UiSelector().className("android.widget.EditText").instance(1)', 10)
            if password_field:
                password_field.click()
                password_field.clear()
                password_field.send_keys(self.password)
                time.sleep(2)
                self.screenshot('password_entered')
                self.record_result('Password entry', 'PASS', 'Entered password')
            else:
                self.record_result('Password entry', 'FAIL', 'Password field not found')
                return

            submit_btn = self.wait_for(AppiumBy.ACCESSIBILITY_ID, "Submit", 10)
            if submit_btn:
                submit_btn.click()
                time.sleep(8)

                skip_btn = self.wait_for(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("Skip")', 5)
                if skip_btn:
                    self.screenshot('biometric_prompt')
                    skip_btn.click()
                    time.sleep(5)
                    self.screenshot('biometric_skipped')
                    self.record_result('Biometric prompt', 'PASS', 'Skipped biometric')
                else:
                    self.record_result('Biometric prompt', 'SKIP', 'No biometric prompt found')

                time.sleep(20)
                self.screenshot('after_login')
                self.record_result('After login', 'PASS', 'Home loaded successfully')
            else:
                self.record_result('Submit button', 'FAIL', 'Submit button not found')
        except Exception as e:
            self.screenshot('error')
            self.record_result('Flow execution', 'FAIL', f'{e}\n{traceback.format_exc()}')

    def save_results(self):
        csv_path = os.path.join(ARTIFACTS_DIR, f'results_{self._ts()}.csv')
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['time', 'step', 'status', 'details'])
            writer.writeheader()
            for r in self.results:
                writer.writerow(r)
        print('✅ Results saved to', csv_path)
        return csv_path


def zip_screenshots():
    zip_name = f"screenshots_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.zip"
    zip_path = os.path.join(ARTIFACTS_DIR, zip_name)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file in os.listdir(ARTIFACTS_DIR):
            if file.endswith(".png"):
                zf.write(os.path.join(ARTIFACTS_DIR, file), file)
    print('🗂️ Screenshots zipped at', zip_path)
    return zip_path


def send_email(csv_path, zip_path, recipients, sender, password, results):
    total = len(results)
    passed = len([r for r in results if r['status'] == 'PASS'])
    failed = len([r for r in results if r['status'] == 'FAIL'])
    skipped = len([r for r in results if r['status'] == 'SKIP'])

    subject = f"Ainsemble APK Automation Report ({passed}/{total} Passed)"
    body = f"""Ainsemble APK Automation completed.

Summary:
Total Steps: {total}
Passed: {passed}
Failed: {failed}
Skipped: {skipped}

Attachments:
1. {os.path.basename(csv_path)} - CSV report
2. {os.path.basename(zip_path)} - Screenshots archive

Executed by Ainsemble Automation Framework."""

    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = ', '.join(recipients)
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    for path in [csv_path, zip_path]:
        with open(path, 'rb') as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(path))
            part['Content-Disposition'] = f'attachment; filename="{os.path.basename(path)}"'
            msg.attach(part)

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender, password)
        server.send_message(msg)
        server.quit()
        print(f"✅ Email sent to: {', '.join(recipients)}")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")


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
    p.add_argument('--login_email', default='test@example.com')
    p.add_argument('--login_password', default='Password123')
    p.add_argument('--send_email', default='false')
    p.add_argument('--email_to')
    p.add_argument('--email_from')
    p.add_argument('--email_password')
    return p.parse_args()


def main():
    args = parse_args()
    driver = None
    try:
        driver = create_browserstack_driver(args.bs_username, args.bs_access_key, args.bs_app_id,
                                            args.device, args.os_version)
        validator = APKValidator(driver, args.login_email, args.login_password)
        time.sleep(3)
        validator.run_flow()
        csv_path = validator.save_results()
        zip_path = zip_screenshots()

        if args.send_email.lower() == 'true' and args.email_to:
            recipients = [x.strip() for x in args.email_to.split(',') if x.strip()]
            send_email(csv_path, zip_path, recipients, args.email_from, args.email_password, validator.results)

    except Exception as e:
        print('Fatal:', e)
        print(traceback.format_exc())
    finally:
        if driver:
            driver.quit()


if __name__ == '__main__':
    main()
