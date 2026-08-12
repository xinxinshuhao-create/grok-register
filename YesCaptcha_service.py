import os

import time

import requests

from dotenv import load_dotenv



load_dotenv()



YESCAPTCHA_SOFT_ID = 102154





class TurnstileService:

    def __init__(self):

        self.yescaptcha_key = os.getenv('YESCAPTCHA_KEY', '').strip()

        self.yescaptcha_api = "https://api.yescaptcha.com"



    def create_task(self, siteurl, sitekey):

        if not self.yescaptcha_key:

            raise Exception("缺少 YESCAPTCHA_KEY，无法创建任务")

        url = f"{self.yescaptcha_api}/createTask"

        payload = {

            "clientKey": self.yescaptcha_key,

            "task": {

                "type": "TurnstileTaskProxyless",

                "websiteURL": siteurl,

                "websiteKey": sitekey

            },

            "softID": YESCAPTCHA_SOFT_ID,

        }

        response = requests.post(url, json=payload)

        response.raise_for_status()

        data = response.json()

        if data.get('errorId') != 0:

            raise Exception(f"YesCaptcha创建任务失败: {data.get('errorDescription')}")

        return data['taskId']


    def get_response(self, task_id, max_retries=30, initial_delay=5, retry_delay=2):
        if not self.yescaptcha_key:
            raise Exception("缺少 YESCAPTCHA_KEY，无法获取结果")

        time.sleep(initial_delay)

        for _ in range(max_retries):
            try:
                url = f"{self.yescaptcha_api}/getTaskResult"
                payload = {
                    "clientKey": self.yescaptcha_key,
                    "taskId": task_id
                }
                response = requests.post(url, json=payload)
                response.raise_for_status()
                data = response.json()

                if data.get('errorId') != 0:
                    print(f"YesCaptcha获取结果失败: {data.get('errorDescription')}")
                    return None

                status = data.get('status')
                if status == 'ready':
                    token = data.get('solution', {}).get('token')
                    if token:
                        return token
                    print("YesCaptcha返回结果中没有token")
                    return None
                elif status == 'processing':
                    time.sleep(retry_delay)
                else:
                    print(f"YesCaptcha未知状态: {status}")
                    time.sleep(retry_delay)
            except Exception as e:
                print(f"获取Turnstile响应异常: {e}")
                time.sleep(retry_delay)

        return None

