import requests

r = requests.post(
    'http://127.0.0.1:8000/outbox/enqueue',
    json={
        'target_contacts': ['geyongpan'],
        'review_text': '测试评价：衣服质量很好，很满意！',
        'image_paths': []
    }
)
print(r.json())
