import requests

url = "http://127.0.0.1:8000/copywriter/invoke"
payload = {
    "input": {
        "topic": "测试主题"
    }
}
try:
    response = requests.post(url, json=payload)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.json()}")
except Exception as e:
    print(f"Error: {e}")
