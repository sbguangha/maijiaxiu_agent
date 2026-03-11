import os
import requests
import json
from dotenv import load_dotenv

# 加载 .env 环境变量
load_dotenv()

# 从 .env 读取飞书配置
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET")
FEISHU_APP_TOKEN = os.getenv("FEISHU_APP_TOKEN")
FEISHU_TABLE_ID = os.getenv("FEISHU_TABLE_ID")

def get_tenant_access_token():
    """获取飞书 tenant_access_token (写表必需的通行证)"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    headers = {"Content-Type": "application/json; charset=utf-8"}
    payload = {
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET
    }
    response = requests.post(url, headers=headers, json=payload)
    data = response.json()
    
    if data.get("code") == 0:
        return data.get("tenant_access_token")
    else:
        print(f"❌ 获取 Token 失败: {data}")
        return None

def write_test_row_to_feishu():
    """像飞书多维表格插入一条测试买家秀"""
    print("1. 正在获取飞书接口权限...")
    token = get_tenant_access_token()
    if not token:
        return

    print("2. 权限获取成功，准备写入数据...")
    
    # 飞书新增多行记录 API 的固定 URL (里面填入了我们保存的 AppToken 和 TableID)
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{FEISHU_TABLE_ID}/records/batch_create"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    # 测试用的假买家秀数据
    # 注意："fields" 里面的键名，必须和你飞书表格的第一行的列名一字不差！
    # 如果你的表格第一列叫 "商品名称"，这里就必须写 "商品名称"。
    payload = {
        "records": [
            {
                "fields": {
                    "商品名称": "[测试记录] 碎花收腰连衣裙",
                    "商品链接": "https://item.taobao.com/item.htm?id=800347429656",
                    "评价内容": "测试 Agent 自动写入功能！绝绝子，质量非常好，非常满意！",
                    "配图建议": "对镜自拍，展示修身效果"
                }
            }
        ]
    }

    print('3. 正在向表格发送网络请求...')
    response = requests.post(url, headers=headers, json=payload)
    result = response.json()

    # 判断是否成功
    if result.get("code") == 0:
        print("\n🎉🎉 大功告成！一条数据已经成功写入你的飞书表格！")
        print("快去你的浏览器里打开那个飞书表格看看！")
    else:
        print("\n❌ 写入失败！看看飞书返回的错误原因：")
        print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    print(f"APP_ID 是否读取成功: {'是' if FEISHU_APP_ID else '否'}")
    print(f"APP_TOKEN 是否读取成功: {'是' if FEISHU_APP_TOKEN else '否'}")
    if FEISHU_APP_ID and FEISHU_APP_TOKEN:
        print("-" * 50)
        write_test_row_to_feishu()
    else:
        print("请检查 .env 文件中的飞书配置是否正确填写！")
