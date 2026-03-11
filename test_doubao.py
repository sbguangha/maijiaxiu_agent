"""
单独测试 doubao-seedream 图生图 API
用来排查到底什么参数格式能通
"""
import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("DOUBAO_API_KEY")
API_URL = "https://ark.cn-beijing.volces.com/api/v3/images/generations"

print(f"API_KEY 前8位: {API_KEY[:8]}..." if API_KEY else "❌ DOUBAO_API_KEY 未设置！")

# 测试 1: 纯文生图（不带 image 参数），确认 API Key 可用
print("\n===== 测试 1: 纯文生图（不传 image）=====")
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}"
}

payload_text = {
    "model": "doubao-seedream-4-5-251128",
    "prompt": "一只可爱的橘猫趴在草地上晒太阳",
    "sequential_image_generation": "disabled",
    "response_format": "url",
    "size": "2K",
    "stream": False,
    "watermark": False
}

try:
    resp = requests.post(API_URL, headers=headers, json=payload_text, timeout=120)
    print(f"HTTP 状态码: {resp.status_code}")
    data = resp.json()
    print(f"返回内容: {json.dumps(data, ensure_ascii=False, indent=2)}")
    if "data" in data and data["data"]:
        print(f"✅ 纯文生图成功！URL: {data['data'][0].get('url', 'N/A')[:80]}...")
    else:
        print("❌ 纯文生图失败")
except Exception as e:
    print(f"异常: {e}")

# 测试 2: 图生图（用官方文档里的示例图片 URL）
print("\n===== 测试 2: 图生图（用官方示例 URL）=====")
payload_img = {
    "model": "doubao-seedream-4-5-251128",
    "prompt": "一只可爱的橘猫趴在草地上晒太阳",
    "image": "https://ark-project.tos-cn-beijing.volces.com/doc_image/seedream4_imageToimage.png",
    "sequential_image_generation": "disabled",
    "response_format": "url",
    "size": "2K",
    "stream": False,
    "watermark": False
}

try:
    resp = requests.post(API_URL, headers=headers, json=payload_img, timeout=120)
    print(f"HTTP 状态码: {resp.status_code}")
    data = resp.json()
    print(f"返回内容: {json.dumps(data, ensure_ascii=False, indent=2)}")
    if "data" in data and data["data"]:
        print(f"✅ 图生图成功！URL: {data['data'][0].get('url', 'N/A')[:80]}...")
    else:
        print("❌ 图生图失败")
except Exception as e:
    print(f"异常: {e}")

print("\n===== 测试结束 =====")
